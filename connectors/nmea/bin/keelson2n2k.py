#!/usr/bin/env python3

"""
Command line utility for subscribing to Keelson/Zenoh and outputting NMEA2000 JSON to STDOUT.

Subscribes to specified Keelson subjects on the Zenoh bus, aggregates the data using
skarv, and generates NMEA2000 messages in JSON format written to standard output.

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
# Set to an n2k_gateway.GatewayRunner when running in --gateway mode; while
# None, generated messages are written to STDOUT as NMEA2000 JSON.
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


def output_json(json_str: str):
    """Output a JSON message to STDOUT with proper flushing."""
    # Guard: Skip if json_str is None (e.g., when ARGS not set)
    if json_str is None:
        return

    sys.stdout.write(json_str + "\n")
    sys.stdout.flush()
    logger.debug("Output PGN")


def build_nmea2000_message(pgn: int, pgn_id: str, description: str, fields: list):
    """Build a NMEA2000Message, or None if the connector is not configured.

    Returns None when ARGS is unset — e.g. a skarv trigger firing while another
    test suite has this module imported.
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
    msg.fields = fields
    return msg


def create_nmea2000_message(
    pgn: int, pgn_id: str, description: str, fields: list
) -> str:
    """Create a NMEA2000Message and return its JSON representation."""
    msg = build_nmea2000_message(pgn, pgn_id, description, fields)
    return msg.to_json() if msg is not None else None


def emit(msg):
    """Emit a generated NMEA2000 message.

    In gateway mode the message is injected directly into the CAN gateway; in
    STDOUT mode it is written as NMEA2000 JSON. Building the message and
    emitting it are kept separate so gateway mode never round-trips through
    JSON.
    """
    if msg is None:
        return
    if RUNNER is not None:
        RUNNER.send(msg)
        logger.debug("Injected PGN %s into gateway", msg.PGN)
    else:
        output_json(msg.to_json())


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
                    id="numberOfSatellites",
                    name="Number of Satellites",
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
        description="Subscribe to Keelson/Zenoh and emit NMEA2000 — to a CAN "
        "gateway (--gateway) or as NMEA2000 JSON on STDOUT",
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

    # Gateway mode: inject directly into a CAN gateway instead of STDOUT JSON.
    gateway_group = parser.add_argument_group("CAN gateway (direct mode)")
    gateway_group.add_argument(
        "--gateway",
        choices=sorted(n2k_gateway.GATEWAY_PROFILES),
        help="Inject into this CAN gateway directly. Omit to write NMEA2000 "
        "JSON to STDOUT.",
    )
    gateway_group.add_argument("--host", help="Gateway host (TCP gateway profiles)")
    gateway_group.add_argument(
        "--port", type=int, help="Gateway TCP port (TCP gateway profiles)"
    )
    gateway_group.add_argument(
        "--device", help="Gateway serial device path (USB gateway profiles)"
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
        # Gateway mode: open the CAN gateway (write-only — received frames are
        # dropped rather than queued).
        if ARGS.gateway:
            logger.info("Gateway mode: %s", ARGS.gateway)
            RUNNER = n2k_gateway.GatewayRunner(
                ARGS.gateway,
                host=ARGS.host,
                port=ARGS.port,
                device=ARGS.device,
                stream_received=False,
            )
            RUNNER.start()

        logger.info("Opening Zenoh session...")
        with zenoh.open(conf) as session, GracefulShutdown() as shutdown:
            logger.info(f"Connected to realm: {ARGS.realm}, entity: {ARGS.entity_id}")

            # Wait for the gateway identity probe before subscribing, so no
            # message is generated before the gateway is ready to inject it.
            if RUNNER is not None and not _await_gateway(RUNNER, shutdown):
                return

            # Mirror all subjects to skarv
            for subject in SUBJECTS:
                source_id = getattr(ARGS, f"source_id_{subject}")
                zenoh_key = keelson.construct_pubsub_key(
                    ARGS.realm, ARGS.entity_id, subject, source_id
                )
                mirror(session, zenoh_key, subject)
                logger.info(f"Subscribed to: {zenoh_key}")

            sink = "CAN gateway" if RUNNER is not None else "STDOUT (NMEA2000 JSON)"
            logger.info("Emitting NMEA2000 to %s...", sink)

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
