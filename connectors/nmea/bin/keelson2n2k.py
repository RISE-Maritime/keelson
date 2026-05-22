#!/usr/bin/env python3

"""
Command line utility for subscribing to Keelson/Zenoh and injecting NMEA2000 into a CAN gateway.

Subscribes to specified Keelson subjects on the Zenoh bus, aggregates the data using
skarv, generates NMEA2000 messages, and injects them into a CAN gateway.

Generated PGN types:
- 129025: Position, Rapid Update
- 129026: COG & SOG, Rapid Update
- 129029: GNSS Position Data
- 127250: Vessel Heading
- 127257: Attitude
- 130306: Wind Data
- 127245: Rudder
- 130311: Environmental Parameters
"""

import sys
import logging
import argparse
import concurrent.futures
from datetime import datetime, timezone
from typing import Any, Dict

import zenoh
import skarv
import skarv.utilities
import keelson
from skarv.utilities.zenoh import mirror
from nmea2000.message import NMEA2000Message, NMEA2000Field
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    setup_logging,
    GracefulShutdown,
)
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality

# Sibling module in this bin/ directory.
import n2k_gateway

# Subjects to subscribe to
SUBJECTS = [
    "location_fix",
    "speed_over_ground_knots",
    "course_over_ground_deg",
    "heading_true_north_deg",
    "heading_magnetic_deg",
    "yaw_deg",
    "pitch_deg",
    "roll_deg",
    "yaw_rate_degps",
    "location_fix_hdop",
    "location_fix_satellites_used",
    "location_fix_undulation_m",
    "location_fix_quality",
    "apparent_wind_speed_mps",
    "apparent_wind_angle_deg",
    "true_wind_speed_mps",
    "true_wind_angle_deg",
    "rudder_angle_deg",
    "water_temperature_celsius",
    "air_pressure_pa",
]


def quality_to_n2k_method(quality: LocationFixQuality) -> int:
    """Map a LocationFixQuality message back to a PGN 129029 'method' enum code.

    Prefers rtk_status / pos_type over fix_type so RTK and differential state
    survives the round-trip.
    """
    if quality.fix_type == LocationFixQuality.INVALID:
        return 8  # Simulator mode — no usable fix
    if quality.fix_type == LocationFixQuality.FIX_NO:
        return 0
    if quality.fix_type == LocationFixQuality.DR_ONLY:
        return 6
    if quality.rtk_status == LocationFixQuality.RTK_STATUS_FIXED:
        return 4
    if quality.rtk_status == LocationFixQuality.RTK_STATUS_FLOAT:
        return 5
    if quality.rtk_status == LocationFixQuality.RTK_STATUS_DIFFERENTIAL:
        return 2
    if quality.pos_type == LocationFixQuality.POS_TYPE_RTK_INT:
        return 4
    if quality.pos_type == LocationFixQuality.POS_TYPE_RTK_FLOAT:
        return 5
    if quality.pos_type == LocationFixQuality.POS_TYPE_PSRDIFF:
        return 2
    if quality.pos_type in (
        LocationFixQuality.POS_TYPE_PPP_FLOAT,
        LocationFixQuality.POS_TYPE_PPP_INT,
    ):
        return 3  # Precise GNSS
    if quality.pos_type == LocationFixQuality.POS_TYPE_FIXED:
        return 7  # Manual / fixed
    if quality.pos_type == LocationFixQuality.POS_TYPE_NO_SOLUTION:
        return 0
    return 1  # Single-point GNSS fix


# Inverse of N2K_INTEGRITY_MAP in n2k2keelson.py
QUALITY_INTEGRITY_TO_N2K: Dict[int, int] = {
    LocationFixQuality.INTEGRITY_NO_CHECK: 0,
    LocationFixQuality.INTEGRITY_SAFE: 1,
    LocationFixQuality.INTEGRITY_CAUTION: 2,
    LocationFixQuality.INTEGRITY_UNSAFE: 3,
}

ARGS = None
# The n2k_gateway.GatewayRunner that owns the CAN gateway connection, set by
# main(). While None (before startup, or under test) emit() is a no-op.
RUNNER = None
logger = logging.getLogger("keelson2n2k")


def unpack(sample: skarv.Sample) -> Any:
    """
    Unpacks a skarv sample containing a zenoh payload into a keelson message.
    """
    subject = sample.key_expr
    _, _, payload = keelson.uncover(sample.value.to_bytes())
    return keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(subject)
    )


# The nmea2000 encoder's encode_pgn_* functions look up *every* field of the
# target PGN by id and raise if one is missing. The generate_pgn_* functions
# below only set the data-bearing fields, so build_nmea2000_message completes
# each message before emitting it. PGN_REQUIRED_FIELDS is the full field-id set
# each encoder needs (mirrors the encode_pgn_* sources); the test suite encodes
# every generated PGN, which guards this table against nmea2000 library drift.
PGN_REQUIRED_FIELDS = {
    129025: ["latitude", "longitude"],
    129026: ["sid", "cogReference", "reserved_10", "cog", "sog", "reserved_48"],
    129029: [
        "sid",
        "date",
        "time",
        "latitude",
        "longitude",
        "altitude",
        "gnssType",
        "method",
        "integrity",
        "reserved_258",
        "numberOfSvs",
        "hdop",
        "pdop",
        "geoidalSeparation",
        "referenceStations",
    ],
    127250: ["sid", "heading", "deviation", "variation", "reference", "reserved_58"],
    127257: ["sid", "yaw", "pitch", "roll", "reserved_56"],
    130306: ["sid", "windSpeed", "windAngle", "reference", "reserved_43"],
    127245: [
        "instance",
        "directionOrder",
        "reserved_11",
        "angleOrder",
        "position",
        "reserved_48",
    ],
    130311: [
        "sid",
        "temperatureSource",
        "humiditySource",
        "temperature",
        "humidity",
        "atmosphericPressure",
    ],
}

# The LOOKUP-type fields among PGN_REQUIRED_FIELDS. A LOOKUP field rejects
# value=None (the encoder maps raw_value directly and None has no lookup
# entry), so an omitted one is filled with raw_value=0; every other omitted
# field takes value=None -> the N2K "not available" sentinel.
_LOOKUP_FILLERS = {
    "cogReference",
    "gnssType",
    "method",
    "integrity",
    "reference",
    "directionOrder",
    "temperatureSource",
    "humiditySource",
}


def _complete(pgn: int, fields: list) -> list:
    """Append every encoder-required field the generator did not set."""
    present = {field.id for field in fields}
    for field_id in PGN_REQUIRED_FIELDS.get(pgn, []):
        if field_id in present:
            continue
        if field_id in _LOOKUP_FILLERS:
            fields.append(NMEA2000Field(id=field_id, name=field_id, raw_value=0))
        else:
            fields.append(NMEA2000Field(id=field_id, name=field_id, value=None))
    return fields


def build_nmea2000_message(pgn: int, pgn_id: str, description: str, fields: list):
    """Build a NMEA2000Message, or None if the connector is not configured.

    Returns None when ARGS or RUNNER is unset — e.g. a skarv trigger firing
    while another test suite has this module imported.
    """
    if ARGS is None:
        return None

    msg = NMEA2000Message(
        PGN=pgn,
        id=pgn_id,
        description=description,
        source=ARGS.source_address,
        destination=255,  # Broadcast
        priority=ARGS.priority,
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = _complete(pgn, fields)
    return msg


def _report_injection(pgn, future) -> None:
    """Done-callback for an inject: log the encode + transmit outcome.

    Runs on the gateway loop thread once ``AsyncIOClient.send`` completes. A
    transmit failure (lost socket, unplugged device) raises inside the client
    and arrives here as the future's exception, so a dropped frame is logged
    at ERROR instead of being silently lost. Encode failures are *not* visible
    here -- the client catches the ``ValueError``, logs an ``nmea2000.ioclient``
    WARNING and returns -- so a clean result confirms the transmit half only.
    """
    try:
        error = future.exception()
    except concurrent.futures.CancelledError:
        logger.warning("Inject of PGN %s cancelled before transmit", pgn)
        return
    if error is not None:
        logger.error("Failed to inject PGN %s: %s", pgn, error)
    else:
        logger.debug("Injected PGN %s", pgn)


def emit(msg):
    """Inject a generated NMEA2000 message into the CAN gateway."""
    if msg is None or RUNNER is None:
        return
    # The encode + transmit happens later on the gateway thread. Attach a
    # done-callback so transmit failures are reported back to this connector's
    # log instead of vanishing on the gateway thread.
    pgn = msg.PGN
    future = RUNNER.send(msg)
    future.add_done_callback(lambda fut: _report_injection(pgn, fut))


@skarv.trigger("location_fix")
def generate_pgn_129025():
    """
    Generate PGN 129025: Position, Rapid Update
    Triggered when location_fix updates
    """
    location_sample = skarv.get("location_fix")
    if not location_sample:
        return

    location = unpack(location_sample)
    if not location:
        return

    fields = [
        NMEA2000Field(
            id="latitude",
            name="Latitude",
            value=location.latitude,
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="longitude",
            name="Longitude",
            value=location.longitude,
            unit_of_measurement="deg",
        ),
    ]

    emit(
        build_nmea2000_message(
            129025, "positionRapidUpdate", "Position, Rapid Update", fields
        )
    )


@skarv.trigger("course_over_ground_deg")
@skarv.trigger("speed_over_ground_knots")
def generate_pgn_129026():
    """
    Generate PGN 129026: COG & SOG, Rapid Update
    Triggered when COG or SOG updates
    """
    cog_sample = skarv.get("course_over_ground_deg")
    sog_sample = skarv.get("speed_over_ground_knots")

    if not cog_sample or not sog_sample:
        return

    cog = unpack(cog_sample)
    sog = unpack(sog_sample)

    if not cog or not sog:
        return

    # Convert to appropriate units
    cog_rad = cog.value * 3.14159265359 / 180.0  # deg to rad
    sog_ms = sog.value / 1.94384  # knots to m/s

    fields = [
        NMEA2000Field(
            id="cog",
            name="COG",
            value=cog_rad,
            unit_of_measurement="rad",
        ),
        NMEA2000Field(
            id="sog",
            name="SOG",
            value=sog_ms,
            unit_of_measurement="m/s",
        ),
    ]

    emit(
        build_nmea2000_message(
            129026, "cogSogRapidUpdate", "COG & SOG, Rapid Update", fields
        )
    )


@skarv.trigger("location_fix")
@skarv.trigger("location_fix_satellites_used")
@skarv.trigger("location_fix_hdop")
@skarv.trigger("location_fix_quality")
def generate_pgn_129029():
    """
    Generate PGN 129029: GNSS Position Data
    Triggered when GNSS data updates
    """
    location_sample = skarv.get("location_fix")
    if not location_sample:
        return

    location = unpack(location_sample)
    if not location:
        return

    fields = [
        NMEA2000Field(
            id="latitude",
            name="Latitude",
            value=location.latitude,
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="longitude",
            name="Longitude",
            value=location.longitude,
            unit_of_measurement="deg",
        ),
    ]

    # Add optional fields if available
    sats_sample = skarv.get("location_fix_satellites_used")
    if sats_sample:
        sats = unpack(sats_sample)
        if sats:
            fields.append(
                NMEA2000Field(
                    id="numberOfSvs",
                    name="Number of SVs",
                    value=sats.value,
                )
            )

    hdop_sample = skarv.get("location_fix_hdop")
    if hdop_sample:
        hdop = unpack(hdop_sample)
        if hdop:
            fields.append(
                NMEA2000Field(
                    id="hdop",
                    name="HDOP",
                    value=hdop.value,
                )
            )

    undulation_sample = skarv.get("location_fix_undulation_m")
    if undulation_sample:
        undulation = unpack(undulation_sample)
        if undulation:
            fields.append(
                NMEA2000Field(
                    id="geoidalSeparation",
                    name="Geoidal Separation",
                    value=undulation.value,
                    unit_of_measurement="m",
                )
            )

    quality_sample = skarv.get("location_fix_quality")
    if quality_sample:
        quality = unpack(quality_sample)
        if quality:
            fields.append(
                NMEA2000Field(
                    id="method",
                    name="GNSS Method",
                    value=quality_to_n2k_method(quality),
                )
            )
            integrity_code = QUALITY_INTEGRITY_TO_N2K.get(quality.integrity)
            if integrity_code is not None:
                fields.append(
                    NMEA2000Field(
                        id="integrity",
                        name="Integrity",
                        value=integrity_code,
                    )
                )

    emit(
        build_nmea2000_message(129029, "gnssPositionData", "GNSS Position Data", fields)
    )


@skarv.trigger("heading_true_north_deg")
@skarv.trigger("heading_magnetic_deg")
def generate_pgn_127250():
    """
    Generate PGN 127250: Vessel Heading
    Triggered when heading updates
    """
    # Prefer true heading over magnetic
    heading_sample = skarv.get("heading_true_north_deg")
    reference = "True"

    if not heading_sample:
        heading_sample = skarv.get("heading_magnetic_deg")
        reference = "Magnetic"

    if not heading_sample:
        return

    heading = unpack(heading_sample)
    if not heading:
        return

    # Convert to radians
    heading_rad = heading.value * 3.14159265359 / 180.0

    fields = [
        NMEA2000Field(
            id="heading",
            name="Heading",
            value=heading_rad,
            unit_of_measurement="rad",
        ),
        NMEA2000Field(
            id="reference",
            name="Reference",
            value=reference,
        ),
    ]

    emit(build_nmea2000_message(127250, "vesselHeading", "Vessel Heading", fields))


@skarv.trigger("yaw_deg")
@skarv.trigger("pitch_deg")
@skarv.trigger("roll_deg")
def generate_pgn_127257():
    """
    Generate PGN 127257: Attitude
    Triggered when attitude data updates
    """
    yaw_sample = skarv.get("yaw_deg")
    pitch_sample = skarv.get("pitch_deg")
    roll_sample = skarv.get("roll_deg")

    # Need at least one value
    if not any([yaw_sample, pitch_sample, roll_sample]):
        return

    fields = []

    if yaw_sample:
        yaw = unpack(yaw_sample)
        if yaw:
            yaw_rad = yaw.value * 3.14159265359 / 180.0
            fields.append(
                NMEA2000Field(
                    id="yaw",
                    name="Yaw",
                    value=yaw_rad,
                    unit_of_measurement="rad",
                )
            )

    if pitch_sample:
        pitch = unpack(pitch_sample)
        if pitch:
            pitch_rad = pitch.value * 3.14159265359 / 180.0
            fields.append(
                NMEA2000Field(
                    id="pitch",
                    name="Pitch",
                    value=pitch_rad,
                    unit_of_measurement="rad",
                )
            )

    if roll_sample:
        roll = unpack(roll_sample)
        if roll:
            roll_rad = roll.value * 3.14159265359 / 180.0
            fields.append(
                NMEA2000Field(
                    id="roll",
                    name="Roll",
                    value=roll_rad,
                    unit_of_measurement="rad",
                )
            )

    if fields:
        emit(build_nmea2000_message(127257, "attitude", "Attitude", fields))


@skarv.trigger("apparent_wind_speed_mps")
@skarv.trigger("apparent_wind_angle_deg")
@skarv.trigger("true_wind_speed_mps")
@skarv.trigger("true_wind_angle_deg")
def generate_pgn_130306():
    """
    Generate PGN 130306: Wind Data
    Triggered when wind data updates
    """
    # Prefer apparent wind, fall back to true wind
    speed_sample = skarv.get("apparent_wind_speed_mps")
    angle_sample = skarv.get("apparent_wind_angle_deg")
    reference = "Apparent"

    if not speed_sample or not angle_sample:
        speed_sample = skarv.get("true_wind_speed_mps")
        angle_sample = skarv.get("true_wind_angle_deg")
        reference = "True (ground referenced)"

    if not speed_sample or not angle_sample:
        return

    speed = unpack(speed_sample)
    angle = unpack(angle_sample)

    if not speed or not angle:
        return

    # Convert to appropriate units
    # Speed is already in m/s from Keelson - no conversion needed
    speed_ms = speed.value
    angle_rad = angle.value * 3.14159265359 / 180.0  # deg to rad

    fields = [
        NMEA2000Field(
            id="windSpeed",
            name="Wind Speed",
            value=speed_ms,
            unit_of_measurement="m/s",
        ),
        NMEA2000Field(
            id="windAngle",
            name="Wind Angle",
            value=angle_rad,
            unit_of_measurement="rad",
        ),
        NMEA2000Field(
            id="reference",
            name="Reference",
            value=reference,
        ),
    ]

    emit(build_nmea2000_message(130306, "windData", "Wind Data", fields))


@skarv.trigger("rudder_angle_deg")
def generate_pgn_127245():
    """
    Generate PGN 127245: Rudder
    Triggered when rudder angle updates
    """
    rudder_sample = skarv.get("rudder_angle_deg")
    if not rudder_sample:
        return

    rudder = unpack(rudder_sample)
    if not rudder:
        return

    # Convert to radians
    angle_rad = rudder.value * 3.14159265359 / 180.0

    fields = [
        NMEA2000Field(
            id="position",
            name="Position",
            value=angle_rad,
            unit_of_measurement="rad",
        ),
    ]

    emit(build_nmea2000_message(127245, "rudder", "Rudder", fields))


@skarv.trigger("water_temperature_celsius")
@skarv.trigger("air_pressure_pa")
def generate_pgn_130311():
    """
    Generate PGN 130311: Environmental Parameters
    Triggered when environmental data updates
    """
    temp_sample = skarv.get("water_temperature_celsius")
    pressure_sample = skarv.get("air_pressure_pa")

    # Need at least one value
    if not temp_sample and not pressure_sample:
        return

    fields = []

    if temp_sample:
        temp = unpack(temp_sample)
        if temp:
            # Convert Celsius to Kelvin
            temp_k = temp.value + 273.15
            fields.append(
                NMEA2000Field(
                    id="temperature",
                    name="Temperature",
                    value=temp_k,
                    unit_of_measurement="K",
                )
            )

    if pressure_sample:
        pressure = unpack(pressure_sample)
        if pressure:
            fields.append(
                NMEA2000Field(
                    id="atmosphericPressure",
                    name="Atmospheric Pressure",
                    value=pressure.value,
                    unit_of_measurement="Pa",
                )
            )

    if fields:
        emit(
            build_nmea2000_message(
                130311, "environmentalParameters", "Environmental Parameters", fields
            )
        )


def _await_gateway(runner, shutdown) -> bool:
    """Block until the gateway identity probe completes.

    Returns True once the gateway is identified, False if the gateway thread
    exits first or shutdown is requested while waiting.
    """
    while not shutdown.is_requested():
        if runner.wait_identity(timeout=1.0) is not None:
            return True
        if not runner.is_running():
            logger.error("Gateway thread exited before identifying the gateway")
            return False
    return False


def main():
    global ARGS, RUNNER

    parser = argparse.ArgumentParser(
        prog="keelson2n2k",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Subscribe to Keelson/Zenoh and inject NMEA2000 into a CAN gateway",
    )

    # Add common Zenoh arguments from scaffolding
    add_common_arguments(parser)

    # Keelson identifiers (required)
    parser.add_argument(
        "-r", "--realm", type=str, required=True, help="Keelson realm (base path)"
    )
    parser.add_argument(
        "-e", "--entity-id", type=str, required=True, help="Entity identifier"
    )

    # NMEA2000 configuration
    parser.add_argument(
        "--source-address",
        type=int,
        default=1,
        help="NMEA2000 source address (0-253). Note: a polite gateway rewrites "
        "this to its own claimed address.",
    )
    parser.add_argument(
        "--priority",
        type=int,
        default=2,
        help="NMEA2000 message priority (0-7, lower is higher priority)",
    )

    # CAN gateway selection.
    gateway_group = parser.add_argument_group("CAN gateway")
    gateway_group.add_argument(
        "--gateway",
        required=True,
        choices=sorted(n2k_gateway.GATEWAY_PROFILES),
        help="CAN gateway profile to inject into.",
    )
    gateway_group.add_argument("--host", help="Gateway host (TCP gateway profiles)")
    gateway_group.add_argument(
        "--port", type=int, help="Gateway TCP port (TCP gateway profiles)"
    )
    gateway_group.add_argument(
        "--device", help="Gateway serial device path (USB gateway profiles)"
    )
    gateway_group.add_argument(
        "--ensure-baud",
        type=int,
        default=115200,
        help="NGX-1 target serial baud rate (actisense_ngx1 only)",
    )
    gateway_group.add_argument(
        "--persist",
        action="store_true",
        help="Persist NGX-1 configuration to EEPROM (actisense_ngx1 only)",
    )

    # Per-subject source ID patterns (wildcard support)
    for subject in SUBJECTS:
        parser.add_argument(
            f"--source_id_{subject}",
            type=str,
            default="**",
            help=f"Source ID pattern for {subject} (supports wildcards)",
        )

    ARGS = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=ARGS.log_level)

    # Configure Zenoh using scaffolding
    conf = create_zenoh_config(
        mode=ARGS.mode,
        connect=ARGS.connect,
        listen=ARGS.listen,
    )

    # Initialize Zenoh logging
    zenoh.init_log_from_env_or(logging.getLevelName(ARGS.log_level))

    try:
        # Open the CAN gateway (write-only — received frames are dropped
        # rather than queued).
        logger.info("Gateway: %s", ARGS.gateway)
        RUNNER = n2k_gateway.GatewayRunner(
            ARGS.gateway,
            host=ARGS.host,
            port=ARGS.port,
            device=ARGS.device,
            stream_received=False,
            ensure_baud=ARGS.ensure_baud,
            persist=ARGS.persist,
        )
        RUNNER.start()

        logger.info("Opening Zenoh session...")
        with zenoh.open(conf) as session, GracefulShutdown() as shutdown:
            logger.info(f"Connected to realm: {ARGS.realm}, entity: {ARGS.entity_id}")

            # Wait for the gateway identity probe before subscribing, so no
            # message is generated before the gateway is ready to inject it.
            if not _await_gateway(RUNNER, shutdown):
                return

            # Mirror all subjects to skarv
            for subject in SUBJECTS:
                source_id = getattr(ARGS, f"source_id_{subject}")
                zenoh_key = keelson.construct_pubsub_key(
                    ARGS.realm, ARGS.entity_id, subject, source_id
                )
                mirror(session, zenoh_key, subject)
                logger.info(f"Subscribed to: {zenoh_key}")

            logger.info("Injecting NMEA2000 into the CAN gateway...")

            # Keep running until interrupted
            while not shutdown.is_requested():
                shutdown.wait(1.0)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    finally:
        if RUNNER is not None:
            RUNNER.stop()


if __name__ == "__main__":
    main()
