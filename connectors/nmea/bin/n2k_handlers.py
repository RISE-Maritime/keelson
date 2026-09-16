#!/usr/bin/env python3

"""
Shared NMEA 2000 PGN handlers: decoded NMEA2000Message → Keelson subjects.

Used by n2k2keelson (CAN gateways) and nmea01832keelson ($PCDIN / $MXPGN
sentences). A library module rather than an entry point, so the Docker image
keeps its .py extension and siblings can import it.

Supported PGNs:
- 129025: Position, Rapid Update
- 129026: COG & SOG, Rapid Update
- 129029: GNSS Position Data
- 127250: Vessel Heading
- 127257: Attitude
- 130306: Wind Data
- 127245: Rudder
- 130311: Environmental Parameters
- 127488 / 127489: Engine Parameters, Rapid Update / Dynamic
- 127505: Fluid Level
- 127506: DC Detailed Status
- 127508: Battery Status
- 128259: Speed
- 128267: Water Depth
- 130312 / 130316: Temperature (sea, outside, dew point)
- 130313: Humidity (outside)
- 130314: Actual Pressure (atmospheric)
- 129038: AIS Class A Position Report
- 129039: AIS Class B Position Report
- 129794: AIS Class A Static and Voyage Related Data
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Callable

from nmea2000.message import NMEA2000Message

import keelson
from keelson.scaffolding import declare_publisher
from keelson.helpers import (
    enclose_from_float,
    enclose_from_integer,
    enclose_from_lon_lat,
    enclose_from_string,
    enclose_from_timestamp,
)
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality
from keelson.payloads.VesselNavStatus_pb2 import VesselNavStatus
from keelson.payloads.VesselType_pb2 import VesselType as VesselTypePb

# Global state
PUBLISHERS: Dict[tuple, Any] = {}  # Cache for lazy publisher creation

logger = logging.getLogger("n2k_handlers")

# Map NMEA-2000 PGN 129029 "method" enum → (FixType, PosType, RtkStatus).
N2K_METHOD_MAP: Dict[int, tuple] = {
    0: (
        LocationFixQuality.FIX_NO,
        LocationFixQuality.POS_TYPE_NO_SOLUTION,
        LocationFixQuality.RTK_STATUS_NONE,
    ),
    1: (
        LocationFixQuality.FIX_3D,
        LocationFixQuality.POS_TYPE_SINGLE,
        LocationFixQuality.RTK_STATUS_NONE,
    ),
    2: (
        LocationFixQuality.FIX_3D,
        LocationFixQuality.POS_TYPE_PSRDIFF,
        LocationFixQuality.RTK_STATUS_DIFFERENTIAL,
    ),
    3: (
        LocationFixQuality.FIX_3D,
        LocationFixQuality.POS_TYPE_PPP_INT,
        LocationFixQuality.RTK_STATUS_NONE,
    ),
    4: (
        LocationFixQuality.FIX_3D,
        LocationFixQuality.POS_TYPE_RTK_INT,
        LocationFixQuality.RTK_STATUS_FIXED,
    ),
    5: (
        LocationFixQuality.FIX_3D,
        LocationFixQuality.POS_TYPE_RTK_FLOAT,
        LocationFixQuality.RTK_STATUS_FLOAT,
    ),
    6: (
        LocationFixQuality.DR_ONLY,
        LocationFixQuality.POS_TYPE_UNKNOWN,
        LocationFixQuality.RTK_STATUS_NONE,
    ),
    7: (
        LocationFixQuality.FIX_3D,
        LocationFixQuality.POS_TYPE_FIXED,
        LocationFixQuality.RTK_STATUS_NONE,
    ),
    8: (
        LocationFixQuality.INVALID,
        LocationFixQuality.POS_TYPE_UNKNOWN,
        LocationFixQuality.RTK_STATUS_NONE,
    ),
}

# N2K PGN 129029 integrity → proto Integrity enum.
N2K_INTEGRITY_MAP: Dict[int, int] = {
    0: LocationFixQuality.INTEGRITY_NO_CHECK,
    1: LocationFixQuality.INTEGRITY_SAFE,
    2: LocationFixQuality.INTEGRITY_CAUTION,
    3: LocationFixQuality.INTEGRITY_UNSAFE,
}

# Map N2K method/integrity *names* (string lookups) to their integer codes,
# in case nmea2000 returns the resolved string instead of the raw enum int.
N2K_METHOD_NAME_TO_CODE: Dict[str, int] = {
    "No GNSS": 0,
    "GNSS fix": 1,
    "DGNSS fix": 2,
    "Precise GNSS": 3,
    "RTK Fixed Integer": 4,
    "RTK float": 5,
    "Estimated (DR) mode": 6,
    "Manual Input": 7,
    "Simulator mode": 8,
}
N2K_INTEGRITY_NAME_TO_CODE: Dict[str, int] = {
    "No integrity checking": 0,
    "Safe": 1,
    "Caution": 2,
    "Unsafe": 3,
}


def get_or_create_publisher(
    session,
    realm: str,
    entity_id: str,
    subject: str,
    source_id: str,
    target_id: str = None,
):
    """
    Get or create a Zenoh publisher for the specified subject.

    Publishers are cached globally to avoid recreating them for each message.
    A ``target_id`` adds the ``@target`` key extension, used to scope a subject
    to one observed vessel (e.g. an AIS contact keyed by MMSI).
    """
    key = (realm, entity_id, subject, source_id, target_id)
    if key not in PUBLISHERS:
        key_expr = keelson.construct_pubsub_key(
            realm, entity_id, subject, source_id, target_id=target_id
        )
        PUBLISHERS[key] = declare_publisher(session, key_expr)
        logger.info(f"Created publisher for {key_expr}")
    return PUBLISHERS[key]


def publish_to_keelson(
    session,
    realm: str,
    entity_id: str,
    subject: str,
    source_id: str,
    value: bytes,
    target_id: str = None,
):
    """Publish a value to a Keelson subject, optionally scoped to a target."""
    publisher = get_or_create_publisher(
        session, realm, entity_id, subject, source_id, target_id
    )
    publisher.put(value)
    logger.debug(f"Published to {subject}")


def get_timestamp_ns(timestamp) -> int:
    """Convert timestamp to nanoseconds, handling both datetime objects and strings"""
    if timestamp is None:
        return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    elif isinstance(timestamp, datetime):
        return int(timestamp.timestamp() * 1_000_000_000)
    elif isinstance(timestamp, str):
        # Parse ISO format timestamp string
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    else:
        # Fallback to current time
        return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)


def handle_pgn_129025(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129025: Position, Rapid Update

    Fields: latitude, longitude
    Keelson subject: location_fix
    """
    try:
        latitude = None
        longitude = None

        for field in msg.fields:
            if field.id == "latitude":
                latitude = field.value
            elif field.id == "longitude":
                longitude = field.value

        if latitude is not None and longitude is not None:
            # Use message timestamp if available, convert to nanoseconds
            timestamp_ns = get_timestamp_ns(msg.timestamp)

            envelope = enclose_from_lon_lat(longitude, latitude, timestamp_ns)
            publish_to_keelson(
                session, realm, entity_id, "location_fix", source_id, envelope
            )

            logger.debug(f"Published location: lat={latitude}, lon={longitude}")

    except Exception as e:
        logger.error(f"Error handling PGN 129025: {e}")


def handle_pgn_129026(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129026: COG & SOG, Rapid Update

    Fields: cog (Course Over Ground), sog (Speed Over Ground)
    Keelson subjects: course_over_ground_deg, speed_over_ground_knots
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        for field in msg.fields:
            if field.id == "cog" and field.value is not None:
                # COG in radians, convert to degrees
                cog_deg = field.value
                if field.unit_of_measurement == "rad":
                    cog_deg = field.value * 180.0 / 3.14159265359

                envelope = enclose_from_float(cog_deg, timestamp)
                publish_to_keelson(
                    session,
                    realm,
                    entity_id,
                    "course_over_ground_deg",
                    source_id,
                    envelope,
                )
                logger.debug(f"Published COG: {cog_deg} deg")

            elif field.id == "sog" and field.value is not None:
                # SOG in m/s, convert to knots
                sog_knots = field.value
                if field.unit_of_measurement == "m/s":
                    sog_knots = field.value * 1.94384

                envelope = enclose_from_float(sog_knots, timestamp)
                publish_to_keelson(
                    session,
                    realm,
                    entity_id,
                    "speed_over_ground_knots",
                    source_id,
                    envelope,
                )
                logger.debug(f"Published SOG: {sog_knots} kts")

    except Exception as e:
        logger.error(f"Error handling PGN 129026: {e}")


def _resolve_n2k_code(value, name_map: Dict[str, int]):
    """Return the integer code for a PGN field that may be either int or string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return name_map.get(value)
    return None


def handle_pgn_129029(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129029: GNSS Position Data

    Fields: latitude, longitude, numberOfSvs, hdop, geoidalSeparation,
            method, integrity
    Keelson subjects: location_fix, location_fix_satellites_used,
                     location_fix_hdop, location_fix_undulation_m,
                     location_fix_quality
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        latitude = None
        longitude = None
        method_raw = None
        integrity_raw = None

        for field in msg.fields:
            if field.id == "latitude" and field.value is not None:
                latitude = field.value
            elif field.id == "longitude" and field.value is not None:
                longitude = field.value
            elif field.id == "numberOfSvs" and field.value is not None:
                envelope = enclose_from_integer(int(field.value), timestamp)
                publish_to_keelson(
                    session,
                    realm,
                    entity_id,
                    "location_fix_satellites_used",
                    source_id,
                    envelope,
                )
            elif field.id == "hdop" and field.value is not None:
                envelope = enclose_from_float(float(field.value), timestamp)
                publish_to_keelson(
                    session, realm, entity_id, "location_fix_hdop", source_id, envelope
                )
            elif field.id == "geoidalSeparation" and field.value is not None:
                envelope = enclose_from_float(float(field.value), timestamp)
                publish_to_keelson(
                    session,
                    realm,
                    entity_id,
                    "location_fix_undulation_m",
                    source_id,
                    envelope,
                )
            elif field.id == "method" and field.value is not None:
                method_raw = field.value
            elif field.id == "integrity" and field.value is not None:
                integrity_raw = field.value

        # Publish location if we have both lat and lon
        if latitude is not None and longitude is not None:
            envelope = enclose_from_lon_lat(longitude, latitude, timestamp)
            publish_to_keelson(
                session, realm, entity_id, "location_fix", source_id, envelope
            )

        # Publish fix quality if at least one of method / integrity is present
        if method_raw is not None or integrity_raw is not None:
            method_code = _resolve_n2k_code(method_raw, N2K_METHOD_NAME_TO_CODE)
            integrity_code = _resolve_n2k_code(
                integrity_raw, N2K_INTEGRITY_NAME_TO_CODE
            )
            fix_type, pos_type, rtk_status = N2K_METHOD_MAP.get(
                method_code,
                (
                    LocationFixQuality.UNKNOWN,
                    LocationFixQuality.POS_TYPE_UNKNOWN,
                    LocationFixQuality.RTK_STATUS_UNKNOWN,
                ),
            )
            integrity = N2K_INTEGRITY_MAP.get(
                integrity_code, LocationFixQuality.INTEGRITY_UNKNOWN
            )
            payload = LocationFixQuality()
            payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
            payload.fix_type = fix_type
            payload.pos_type = pos_type
            payload.rtk_status = rtk_status
            payload.integrity = integrity
            publish_to_keelson(
                session,
                realm,
                entity_id,
                "location_fix_quality",
                source_id,
                keelson.enclose(payload.SerializeToString()),
            )

    except Exception as e:
        logger.error(f"Error handling PGN 129029: {e}")


def handle_pgn_127250(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127250: Vessel Heading

    Fields: heading, reference
    Keelson subjects: heading_true_north_deg or heading_magnetic_deg
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        heading = None
        reference = None

        for field in msg.fields:
            if field.id == "heading" and field.value is not None:
                heading = field.value
                # Convert from radians to degrees if needed
                if field.unit_of_measurement == "rad":
                    heading = field.value * 180.0 / 3.14159265359
            elif field.id == "reference" and field.value is not None:
                reference = field.value

        if heading is not None:
            # Determine subject based on reference
            subject = "heading_true_north_deg"
            if reference and "magnetic" in str(reference).lower():
                subject = "heading_magnetic_deg"

            envelope = enclose_from_float(heading, timestamp)
            publish_to_keelson(session, realm, entity_id, subject, source_id, envelope)
            logger.debug(f"Published heading: {heading} deg ({subject})")

    except Exception as e:
        logger.error(f"Error handling PGN 127250: {e}")


def handle_pgn_127257(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127257: Attitude

    Fields: yaw, pitch, roll
    Keelson subjects: yaw_deg, pitch_deg, roll_deg
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        for field in msg.fields:
            value = field.value
            if value is None:
                continue

            # Convert from radians to degrees if needed
            if field.unit_of_measurement == "rad":
                value = value * 180.0 / 3.14159265359

            if field.id == "yaw":
                envelope = enclose_from_float(value, timestamp)
                publish_to_keelson(
                    session, realm, entity_id, "yaw_deg", source_id, envelope
                )
                logger.debug(f"Published yaw: {value} deg")
            elif field.id == "pitch":
                envelope = enclose_from_float(value, timestamp)
                publish_to_keelson(
                    session, realm, entity_id, "pitch_deg", source_id, envelope
                )
                logger.debug(f"Published pitch: {value} deg")
            elif field.id == "roll":
                envelope = enclose_from_float(value, timestamp)
                publish_to_keelson(
                    session, realm, entity_id, "roll_deg", source_id, envelope
                )
                logger.debug(f"Published roll: {value} deg")

    except Exception as e:
        logger.error(f"Error handling PGN 127257: {e}")


def handle_pgn_130306(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 130306: Wind Data

    Fields: windSpeed, windAngle, reference
    Keelson subjects: apparent_wind_speed_mps, apparent_wind_angle_deg (or true variants)
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        wind_speed = None
        wind_angle = None
        reference = None

        for field in msg.fields:
            if field.id == "windSpeed" and field.value is not None:
                # Wind speed is in m/s, Keelson expects m/s - no conversion needed
                wind_speed = field.value
            elif field.id == "windAngle" and field.value is not None:
                wind_angle = field.value
                # Convert from radians to degrees if needed
                if field.unit_of_measurement == "rad":
                    wind_angle = field.value * 180.0 / 3.14159265359
            elif field.id == "reference" and field.value is not None:
                reference = field.value

        # Determine if apparent or true wind based on reference
        is_apparent = True
        if reference and "true" in str(reference).lower():
            is_apparent = False

        speed_subject = (
            "apparent_wind_speed_mps" if is_apparent else "true_wind_speed_mps"
        )
        angle_subject = (
            "apparent_wind_angle_deg" if is_apparent else "true_wind_angle_deg"
        )

        if wind_speed is not None:
            envelope = enclose_from_float(wind_speed, timestamp)
            publish_to_keelson(
                session, realm, entity_id, speed_subject, source_id, envelope
            )
            logger.debug(f"Published wind speed: {wind_speed} m/s")

        if wind_angle is not None:
            envelope = enclose_from_float(wind_angle, timestamp)
            publish_to_keelson(
                session, realm, entity_id, angle_subject, source_id, envelope
            )
            logger.debug(f"Published wind angle: {wind_angle} deg")

    except Exception as e:
        logger.error(f"Error handling PGN 130306: {e}")


def handle_pgn_127245(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127245: Rudder

    Fields: position (rudder angle)
    Keelson subject: rudder_angle_deg
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        for field in msg.fields:
            if field.id == "position" and field.value is not None:
                angle = field.value
                # Convert from radians to degrees if needed
                if field.unit_of_measurement == "rad":
                    angle = field.value * 180.0 / 3.14159265359

                envelope = enclose_from_float(angle, timestamp)
                publish_to_keelson(
                    session, realm, entity_id, "rudder_angle_deg", source_id, envelope
                )
                logger.debug(f"Published rudder angle: {angle} deg")

    except Exception as e:
        logger.error(f"Error handling PGN 127245: {e}")


def handle_pgn_130311(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 130311: Environmental Parameters

    Fields: temperature (water), atmosphericPressure
    Keelson subjects: water_temperature_celsius, air_pressure_pa
    """
    try:
        timestamp = get_timestamp_ns(msg.timestamp)

        for field in msg.fields:
            if field.id == "temperature" and field.value is not None:
                temp = field.value
                # Convert from Kelvin to Celsius if needed
                if field.unit_of_measurement == "K":
                    temp = field.value - 273.15

                envelope = enclose_from_float(temp, timestamp)
                publish_to_keelson(
                    session,
                    realm,
                    entity_id,
                    "water_temperature_celsius",
                    source_id,
                    envelope,
                )
                logger.debug(f"Published water temperature: {temp} C")

            elif field.id == "atmosphericPressure" and field.value is not None:
                pressure = field.value

                envelope = enclose_from_float(pressure, timestamp)
                publish_to_keelson(
                    session, realm, entity_id, "air_pressure_pa", source_id, envelope
                )
                logger.debug(f"Published air pressure: {pressure} Pa")

    except Exception as e:
        logger.error(f"Error handling PGN 130311: {e}")


# --- Engine, tank, battery, speed, depth and environment PGNs -----------

_KELVIN_OFFSET = 273.15
_PA_TO_PSI = 0.000145038

# PGN 127505 tank type label → source_id chunk
TANK_TYPES: Dict[str, str] = {
    "Fuel": "fuel",
    "Water": "fresh_water",
    "Gray water": "gray_water",
    "Live well": "live_well",
    "Oil": "oil",
    "Black water": "black_water",
}

# PGN 130312 / 130316 temperature source label → Keelson subject
TEMPERATURE_SOURCE_SUBJECTS: Dict[str, str] = {
    "Sea Temperature": "water_temperature_celsius",
    "Outside Temperature": "air_temperature_celsius",
    "Dew Point Temperature": "dew_point_celsius",
}


def _field_map(msg: NMEA2000Message) -> Dict[str, Any]:
    """Map field id → field for the fields of a message that carry a value."""
    return {field.id: field for field in msg.fields if field.value is not None}


def _value(field):
    return field.value


def _celsius(field):
    if field.unit_of_measurement == "K":
        return field.value - _KELVIN_OFFSET
    return field.value


def _psi(field):
    if field.unit_of_measurement == "Pa":
        return field.value * _PA_TO_PSI
    return field.value


def _instance_source_id(msg: NMEA2000Message, source_id: str, *chunks: str) -> str:
    """Append chunks and the instance the device reports to source_id.

    The instance is carried verbatim as the last chunk; see
    protocol-specification.md §2.1.2.
    """
    parts = [source_id, *chunks]
    instance = _field_map(msg).get("instance")
    if instance is not None:
        # The decoder resolves some instances to labels (PGN 127488's "Single
        # Engine or Dual Engine Port"); publish the number the device reported.
        value = instance.value
        if isinstance(value, str) and instance.raw_value is not None:
            value = instance.raw_value
        parts.append(str(value))
    return "/".join(parts)


def _publish_fields(
    msg: NMEA2000Message,
    session,
    realm: str,
    entity_id: str,
    source_id: str,
    mapping: Dict[str, tuple],
):
    """Publish TimestampedFloat subjects from a field id → (subject, convert) map."""
    timestamp = get_timestamp_ns(msg.timestamp)
    fields = _field_map(msg)
    for field_id, (subject, convert) in mapping.items():
        field = fields.get(field_id)
        if field is None:
            continue
        envelope = enclose_from_float(float(convert(field)), timestamp)
        publish_to_keelson(session, realm, entity_id, subject, source_id, envelope)


def handle_pgn_127488(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127488: Engine Parameters, Rapid Update

    Fields: instance, speed
    Keelson subject: engine_rate_rpm (source_id ends with the engine instance)
    """
    try:
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id),
            {"speed": ("engine_rate_rpm", _value)},
        )
    except Exception as e:
        logger.error(f"Error handling PGN 127488: {e}")


def handle_pgn_127489(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127489: Engine Parameters, Dynamic

    Fields: instance, oilPressure, oilTemperature, temperature (coolant),
            coolantPressure, fuelRate
    Keelson subjects: engine_oil_pressure_psi, engine_oil_temperature_celsius,
                     engine_coolant_temperature_celsius,
                     engine_coolant_pressure_psi, engine_fuel_rate_lph
    """
    try:
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id),
            {
                "oilPressure": ("engine_oil_pressure_psi", _psi),
                "oilTemperature": ("engine_oil_temperature_celsius", _celsius),
                "temperature": ("engine_coolant_temperature_celsius", _celsius),
                "coolantPressure": ("engine_coolant_pressure_psi", _psi),
                "fuelRate": ("engine_fuel_rate_lph", _value),
            },
        )
    except Exception as e:
        logger.error(f"Error handling PGN 127489: {e}")


def handle_pgn_127505(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127505: Fluid Level

    Fields: instance, type, level, capacity
    Keelson subjects: tank_level_pct, tank_capacity_l
    (source_id ends with <tank type>/<instance>, e.g. .../fuel/3)
    """
    try:
        tank_type = _field_map(msg).get("type")
        label = tank_type.value if tank_type is not None else "unknown"
        chunk = TANK_TYPES.get(label, str(label).lower().replace(" ", "_"))
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id, chunk),
            {
                "level": ("tank_level_pct", _value),
                "capacity": ("tank_capacity_l", _value),
            },
        )
    except Exception as e:
        logger.error(f"Error handling PGN 127505: {e}")


def handle_pgn_127506(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127506: DC Detailed Status

    Fields: instance, stateOfCharge, timeRemaining
    Keelson subjects: battery_state_of_charge_pct, battery_time_remaining_s
    """
    try:
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id),
            {
                "stateOfCharge": ("battery_state_of_charge_pct", _value),
                "timeRemaining": ("battery_time_remaining_s", _value),
            },
        )
    except Exception as e:
        logger.error(f"Error handling PGN 127506: {e}")


def handle_pgn_127508(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 127508: Battery Status

    Fields: instance, voltage, current, temperature
    Keelson subjects: battery_voltage_v, battery_current_a,
                     battery_temperature_celsius
    """
    try:
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id),
            {
                "voltage": ("battery_voltage_v", _value),
                "current": ("battery_current_a", _value),
                "temperature": ("battery_temperature_celsius", _celsius),
            },
        )
    except Exception as e:
        logger.error(f"Error handling PGN 127508: {e}")


def handle_pgn_128259(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 128259: Speed

    Fields: speedWaterReferenced
    Keelson subject: speed_through_water_knots

    speedGroundReferenced is along the hull, not over-ground speed along the
    track, so it is not published as speed_over_ground_knots.
    """
    try:
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            source_id,
            {"speedWaterReferenced": ("speed_through_water_knots", _field_knots)},
        )
    except Exception as e:
        logger.error(f"Error handling PGN 128259: {e}")


def handle_pgn_128267(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 128267: Water Depth

    Fields: depth (below transducer), offset
    Keelson subjects: depth_below_transducer_m, and depth_below_surface_m
                     (positive offset) or depth_below_keel_m (negative offset)
    """
    try:
        fields = _field_map(msg)
        depth = fields.get("depth")
        if depth is None:
            return

        timestamp = get_timestamp_ns(msg.timestamp)
        publish_to_keelson(
            session,
            realm,
            entity_id,
            "depth_below_transducer_m",
            source_id,
            enclose_from_float(float(depth.value), timestamp),
        )

        offset = fields.get("offset")
        if offset is None or not offset.value:
            return
        subject = "depth_below_surface_m" if offset.value > 0 else "depth_below_keel_m"
        publish_to_keelson(
            session,
            realm,
            entity_id,
            subject,
            source_id,
            enclose_from_float(float(depth.value + offset.value), timestamp),
        )
    except Exception as e:
        logger.error(f"Error handling PGN 128267: {e}")


def _handle_temperature(msg, session, realm, entity_id, source_id, value_field):
    """Publish a temperature PGN whose source maps to a Keelson subject."""
    source = _field_map(msg).get("source")
    subject = TEMPERATURE_SOURCE_SUBJECTS.get(source.value) if source else None
    if subject is None:
        logger.debug(f"Unmapped temperature source in PGN {msg.PGN}: {source}")
        return
    _publish_fields(
        msg,
        session,
        realm,
        entity_id,
        _instance_source_id(msg, source_id),
        {value_field: (subject, _celsius)},
    )


def handle_pgn_130312(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 130312: Temperature

    Fields: instance, source, actualTemperature
    Keelson subjects: water_temperature_celsius (Sea), air_temperature_celsius
                     (Outside), dew_point_celsius (Dew Point)
    """
    try:
        _handle_temperature(
            msg, session, realm, entity_id, source_id, "actualTemperature"
        )
    except Exception as e:
        logger.error(f"Error handling PGN 130312: {e}")


def handle_pgn_130316(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 130316: Temperature, Extended Range

    Same sources and subjects as PGN 130312.
    """
    try:
        _handle_temperature(msg, session, realm, entity_id, source_id, "temperature")
    except Exception as e:
        logger.error(f"Error handling PGN 130316: {e}")


def handle_pgn_130313(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 130313: Humidity

    Fields: instance, source, actualHumidity
    Keelson subject: air_relative_humidity_pct (Outside source only)
    """
    try:
        source = _field_map(msg).get("source")
        if source is None or source.value != "Outside":
            return
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id),
            {"actualHumidity": ("air_relative_humidity_pct", _value)},
        )
    except Exception as e:
        logger.error(f"Error handling PGN 130313: {e}")


def handle_pgn_130314(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 130314: Actual Pressure

    Fields: instance, source, pressure
    Keelson subject: air_pressure_pa (Atmospheric source only)
    """
    try:
        source = _field_map(msg).get("source")
        if source is None or source.value != "Atmospheric":
            return
        _publish_fields(
            msg,
            session,
            realm,
            entity_id,
            _instance_source_id(msg, source_id),
            {"pressure": ("air_pressure_pa", _value)},
        )
    except Exception as e:
        logger.error(f"Error handling PGN 130314: {e}")


# --- AIS PGN decode helpers ---------------------------------------------

_RAD_TO_DEG = 180.0 / 3.14159265359


def _field_degrees(field):
    """Angle / angular-rate field value in degrees (the decoder yields radians)."""
    value = field.value
    if value is not None and field.unit_of_measurement in ("rad", "rad/s"):
        value = value * _RAD_TO_DEG
    return value


def _field_knots(field):
    """Speed field value in knots (the decoder yields m/s)."""
    value = field.value
    if value is not None and field.unit_of_measurement == "m/s":
        value = value * 1.94384
    return value


def _enclose_nav_status(ais_status: int, timestamp: int) -> bytes:
    """Enclose an AIS navigation-status code as a VesselNavStatus payload.

    The keelson VesselNavStatus enum is the AIS code + 1 (0 is reserved for
    UNKNOWN); see VesselNavStatus.proto.
    """
    payload = VesselNavStatus()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.navigation_status = ais_status + 1
    return keelson.enclose(payload.SerializeToString())


def _enclose_vessel_type(ais_ship_type: int, timestamp: int) -> bytes:
    """Enclose an AIS ship-type code as a VesselType payload.

    The keelson VesselType enum values are the AIS ship-type codes directly.
    """
    payload = VesselTypePb()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.vessel_type = ais_ship_type
    return keelson.enclose(payload.SerializeToString())


def _ais_eta_ns(date_field, time_field):
    """Combine PGN 129794 etaDate + etaTime fields into a UTC timestamp (ns).

    Returns None if either part is unavailable or not a usable date/time.
    """
    if date_field is None or time_field is None:
        return None
    eta_date = date_field.value
    eta_time = time_field.value
    if eta_date is None or eta_time is None:
        return None
    try:
        eta = datetime.combine(eta_date, eta_time, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return int(eta.timestamp() * 1_000_000_000)


def _publish_ais_position(fields, publish, timestamp):
    """Publish the position subjects common to PGN 129038 and 129039.

    ``fields`` is an ``{id: NMEA2000Field}`` map and ``publish`` is a
    ``publish(subject, envelope)`` callable already bound to an AIS target.
    """
    latitude = fields.get("latitude")
    longitude = fields.get("longitude")
    if (
        latitude is not None
        and latitude.value is not None
        and longitude is not None
        and longitude.value is not None
    ):
        publish(
            "location_fix",
            enclose_from_lon_lat(longitude.value, latitude.value, timestamp),
        )

    cog = fields.get("cog")
    if cog is not None and cog.value is not None:
        publish(
            "course_over_ground_deg",
            enclose_from_float(_field_degrees(cog), timestamp),
        )

    sog = fields.get("sog")
    if sog is not None and sog.value is not None:
        publish(
            "speed_over_ground_knots",
            enclose_from_float(_field_knots(sog), timestamp),
        )

    heading = fields.get("heading")
    if heading is not None and heading.value is not None:
        publish(
            "heading_true_north_deg",
            enclose_from_float(_field_degrees(heading), timestamp),
        )


def handle_pgn_129038(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129038: AIS Class A Position Report

    Fields: userId (MMSI), latitude, longitude, cog, sog, heading,
            rateOfTurn, navStatus
    Keelson subjects (per AIS target): location_fix, course_over_ground_deg,
            speed_over_ground_knots, heading_true_north_deg, yaw_rate_degps,
            nav_status, mmsi_number
    """
    try:
        fields = {field.id: field for field in msg.fields}

        user_id = fields.get("userId")
        mmsi = user_id.value if user_id is not None else None
        if mmsi is None:
            logger.debug("PGN 129038 without an MMSI; skipping")
            return

        timestamp = get_timestamp_ns(msg.timestamp)
        target_id = f"mmsi_{mmsi}"

        def publish(subject, envelope):
            publish_to_keelson(
                session,
                realm,
                entity_id,
                subject,
                source_id,
                envelope,
                target_id=target_id,
            )

        publish("mmsi_number", enclose_from_integer(int(mmsi), timestamp))
        _publish_ais_position(fields, publish, timestamp)

        rate_of_turn = fields.get("rateOfTurn")
        if rate_of_turn is not None and rate_of_turn.value is not None:
            publish(
                "yaw_rate_degps",
                enclose_from_float(_field_degrees(rate_of_turn), timestamp),
            )

        nav_status = fields.get("navStatus")
        if nav_status is not None and nav_status.raw_value is not None:
            publish(
                "nav_status",
                _enclose_nav_status(int(nav_status.raw_value), timestamp),
            )

        logger.debug(f"Published AIS Class A position for {target_id}")

    except Exception as e:
        logger.error(f"Error handling PGN 129038: {e}")


def handle_pgn_129039(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129039: AIS Class B Position Report

    Like PGN 129038, but the Class B report carries neither navigation
    status nor rate of turn.

    Keelson subjects (per AIS target): location_fix, course_over_ground_deg,
            speed_over_ground_knots, heading_true_north_deg, mmsi_number
    """
    try:
        fields = {field.id: field for field in msg.fields}

        user_id = fields.get("userId")
        mmsi = user_id.value if user_id is not None else None
        if mmsi is None:
            logger.debug("PGN 129039 without an MMSI; skipping")
            return

        timestamp = get_timestamp_ns(msg.timestamp)
        target_id = f"mmsi_{mmsi}"

        def publish(subject, envelope):
            publish_to_keelson(
                session,
                realm,
                entity_id,
                subject,
                source_id,
                envelope,
                target_id=target_id,
            )

        publish("mmsi_number", enclose_from_integer(int(mmsi), timestamp))
        _publish_ais_position(fields, publish, timestamp)

        logger.debug(f"Published AIS Class B position for {target_id}")

    except Exception as e:
        logger.error(f"Error handling PGN 129039: {e}")


def handle_pgn_129794(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129794: AIS Class A Static and Voyage Related Data

    Fields: userId (MMSI), name, callsign, imoNumber, typeOfShip, length,
            beam, draft, etaDate, etaTime, destination
    Keelson subjects (per AIS target): name, call_sign, imo_number,
            vessel_type, length_over_all_m, breadth_over_all_m,
            draught_mean_m, eta, destination, mmsi_number
    """
    try:
        fields = {field.id: field for field in msg.fields}

        user_id = fields.get("userId")
        mmsi = user_id.value if user_id is not None else None
        if mmsi is None:
            logger.debug("PGN 129794 without an MMSI; skipping")
            return

        timestamp = get_timestamp_ns(msg.timestamp)
        target_id = f"mmsi_{mmsi}"

        def publish(subject, envelope):
            publish_to_keelson(
                session,
                realm,
                entity_id,
                subject,
                source_id,
                envelope,
                target_id=target_id,
            )

        publish("mmsi_number", enclose_from_integer(int(mmsi), timestamp))

        name = fields.get("name")
        if name is not None and name.value is not None:
            publish("name", enclose_from_string(str(name.value), timestamp))

        callsign = fields.get("callsign")
        if callsign is not None and callsign.value is not None:
            publish("call_sign", enclose_from_string(str(callsign.value), timestamp))

        destination = fields.get("destination")
        if destination is not None and destination.value is not None:
            publish(
                "destination",
                enclose_from_string(str(destination.value), timestamp),
            )

        imo_number = fields.get("imoNumber")
        if imo_number is not None and imo_number.value is not None:
            publish(
                "imo_number", enclose_from_integer(int(imo_number.value), timestamp)
            )

        type_of_ship = fields.get("typeOfShip")
        if type_of_ship is not None and type_of_ship.raw_value is not None:
            publish(
                "vessel_type",
                _enclose_vessel_type(int(type_of_ship.raw_value), timestamp),
            )

        length = fields.get("length")
        if length is not None and length.value is not None:
            publish(
                "length_over_all_m",
                enclose_from_float(float(length.value), timestamp),
            )

        beam = fields.get("beam")
        if beam is not None and beam.value is not None:
            publish(
                "breadth_over_all_m",
                enclose_from_float(float(beam.value), timestamp),
            )

        draft = fields.get("draft")
        if draft is not None and draft.value is not None:
            publish("draught_mean_m", enclose_from_float(float(draft.value), timestamp))

        eta_ns = _ais_eta_ns(fields.get("etaDate"), fields.get("etaTime"))
        if eta_ns is not None:
            publish("eta", enclose_from_timestamp(eta_ns, timestamp))

        logger.debug(f"Published AIS Class A static data for {target_id}")

    except Exception as e:
        logger.error(f"Error handling PGN 129794: {e}")


# Static, parser-supported subject vocabulary — every subject any handler in
# PGN_HANDLERS can possibly publish, regardless of --include-pgns/
# --exclude-pgns or which PGNs the physical CAN install actually emits.
# Declared unconditionally per capability semantics. Kept in sync manually
# with the `publish_to_keelson(...)` / `publish(...)` call sites above.
N2K_SUPPORTED_SUBJECTS = (
    "location_fix",
    "course_over_ground_deg",
    "speed_over_ground_knots",
    "location_fix_satellites_used",
    "location_fix_hdop",
    "location_fix_undulation_m",
    "location_fix_quality",
    "heading_true_north_deg",
    "heading_magnetic_deg",
    "yaw_deg",
    "pitch_deg",
    "roll_deg",
    "apparent_wind_speed_mps",
    "apparent_wind_angle_deg",
    "true_wind_speed_mps",
    "true_wind_angle_deg",
    "rudder_angle_deg",
    "water_temperature_celsius",
    "air_pressure_pa",
    "yaw_rate_degps",
    "nav_status",
    "mmsi_number",
    "name",
    "call_sign",
    "destination",
    "imo_number",
    "vessel_type",
    "length_over_all_m",
    "breadth_over_all_m",
    "draught_mean_m",
    "eta",
    "engine_rate_rpm",
    "engine_oil_pressure_psi",
    "engine_oil_temperature_celsius",
    "engine_coolant_temperature_celsius",
    "engine_coolant_pressure_psi",
    "engine_fuel_rate_lph",
    "tank_level_pct",
    "tank_capacity_l",
    "battery_state_of_charge_pct",
    "battery_time_remaining_s",
    "battery_voltage_v",
    "battery_current_a",
    "battery_temperature_celsius",
    "speed_through_water_knots",
    "depth_below_transducer_m",
    "depth_below_surface_m",
    "depth_below_keel_m",
    "air_temperature_celsius",
    "dew_point_celsius",
    "air_relative_humidity_pct",
)

# PGN Handler Registry
PGN_HANDLERS: Dict[int, Callable] = {
    129025: handle_pgn_129025,  # Position, Rapid Update
    129026: handle_pgn_129026,  # COG & SOG, Rapid Update
    129029: handle_pgn_129029,  # GNSS Position Data
    127250: handle_pgn_127250,  # Vessel Heading
    127257: handle_pgn_127257,  # Attitude
    130306: handle_pgn_130306,  # Wind Data
    127245: handle_pgn_127245,  # Rudder
    130311: handle_pgn_130311,  # Environmental Parameters
    129038: handle_pgn_129038,  # AIS Class A Position Report
    129039: handle_pgn_129039,  # AIS Class B Position Report
    129794: handle_pgn_129794,  # AIS Class A Static and Voyage Related Data
    127488: handle_pgn_127488,  # Engine Parameters, Rapid Update
    127489: handle_pgn_127489,  # Engine Parameters, Dynamic
    127505: handle_pgn_127505,  # Fluid Level
    127506: handle_pgn_127506,  # DC Detailed Status
    127508: handle_pgn_127508,  # Battery Status
    128259: handle_pgn_128259,  # Speed
    128267: handle_pgn_128267,  # Water Depth
    130312: handle_pgn_130312,  # Temperature
    130313: handle_pgn_130313,  # Humidity
    130314: handle_pgn_130314,  # Actual Pressure
    130316: handle_pgn_130316,  # Temperature, Extended Range
}


# Keys of input already warned about as unparsed, so a PGN or sentence arriving
# at 25 Hz logs once rather than 25 times a second. Capped: corrupted input can
# make the keys unbounded.
UNPARSED_WARNED: set = set()
UNPARSED_WARN_LIMIT = 1000


def warn_unparsed_once(key, message: str, *args) -> None:
    """Log ``message`` at WARNING the first time ``key`` is seen.

    For input the connector receives but cannot turn into Keelson data: an
    unhandled PGN or sentence type, or a line that fails to parse.
    """
    if key in UNPARSED_WARNED:
        return
    if len(UNPARSED_WARNED) >= UNPARSED_WARN_LIMIT:
        return
    UNPARSED_WARNED.add(key)
    logger.warning(message + " (further occurrences not logged)", *args)
    if len(UNPARSED_WARNED) == UNPARSED_WARN_LIMIT:
        logger.warning(
            "%d distinct unparsed inputs warned about; not warning about more",
            UNPARSED_WARN_LIMIT,
        )


def dispatch_message(
    msg: NMEA2000Message,
    session,
    realm: str,
    entity_id: str,
    source_id: str,
):
    """Route a decoded NMEA2000 message to its registered PGN handler."""
    handler = PGN_HANDLERS.get(msg.PGN)
    if handler:
        handler(msg, session, realm, entity_id, source_id)
    else:
        warn_unparsed_once(
            ("pgn", msg.PGN, msg.source),
            "No handler for PGN %s (%s) from src %s",
            msg.PGN,
            msg.id,
            msg.source,
        )
