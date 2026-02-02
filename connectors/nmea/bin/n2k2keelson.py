#!/usr/bin/env python3

"""
Command line utility for parsing NMEA2000 JSON messages from STDIN and publishing to Keelson/Zenoh.

Reads NMEA2000 messages in JSON format (one per line) from standard input,
parses them, and publishes the extracted data to appropriate Keelson subjects on the Zenoh bus.

Supported PGNs:
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
import json
import logging
import argparse
from datetime import datetime, timezone
from typing import Dict, Any, Callable

import zenoh
from nmea2000.message import NMEA2000Message

import keelson
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    setup_logging,
)
from keelson.helpers import (
    enclose_from_float,
    enclose_from_integer,
    enclose_from_lon_lat,
    enclose_from_string,
)

# Global state
PUBLISHERS: Dict[tuple, Any] = {}  # Cache for lazy publisher creation

logger = logging.getLogger("n2k2keelson")


def get_or_create_publisher(
    session, realm: str, entity_id: str, subject: str, source_id: str
):
    """
    Get or create a Zenoh publisher for the specified subject.

    Publishers are cached globally to avoid recreating them for each message.
    """
    key = (realm, entity_id, subject, source_id)
    if key not in PUBLISHERS:
        key_expr = keelson.construct_pubsub_key(realm, entity_id, subject, source_id)
        PUBLISHERS[key] = session.declare_publisher(key_expr)
        logger.info(f"Created publisher for {key_expr}")
    return PUBLISHERS[key]


def publish_to_keelson(
    session,
    realm: str,
    entity_id: str,
    subject: str,
    source_id: str,
    value: bytes,
):
    """Publish a value to a Keelson subject."""
    publisher = get_or_create_publisher(session, realm, entity_id, subject, source_id)
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
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

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


def handle_pgn_129029(
    msg: NMEA2000Message, session, realm: str, entity_id: str, source_id: str
):
    """
    Handle PGN 129029: GNSS Position Data

    Fields: latitude, longitude, numberOfSatellites, hdop, geoidalSeparation
    Keelson subjects: location_fix, location_fix_satellites_used, location_fix_hdop, location_fix_undulation_m
    """
    try:
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

        latitude = None
        longitude = None

        for field in msg.fields:
            if field.id == "latitude" and field.value is not None:
                latitude = field.value
            elif field.id == "longitude" and field.value is not None:
                longitude = field.value
            elif field.id == "numberOfSatellites" and field.value is not None:
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

        # Publish location if we have both lat and lon
        if latitude is not None and longitude is not None:
            envelope = enclose_from_lon_lat(longitude, latitude, timestamp)
            publish_to_keelson(
                session, realm, entity_id, "location_fix", source_id, envelope
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
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

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
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

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
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

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
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

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
        timestamp_dt = msg.timestamp if msg.timestamp else datetime.now(timezone.utc)
        timestamp = int(timestamp_dt.timestamp() * 1_000_000_000)

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
}


def process_message(
    json_line: str,
    session,
    realm: str,
    entity_id: str,
    source_id: str,
    publish_raw: bool,
):
    """Process a single JSON message line"""
    try:
        # Parse JSON
        msg = NMEA2000Message.from_json(json_line)

        logger.debug(f"Received PGN {msg.PGN}: {msg.id}")

        # Publish raw JSON if requested
        if publish_raw:
            envelope = enclose_from_string(json_line)
            publish_to_keelson(session, realm, entity_id, "raw", source_id, envelope)

        # Look up handler for this PGN
        handler = PGN_HANDLERS.get(msg.PGN)
        if handler:
            handler(msg, session, realm, entity_id, source_id)
        else:
            logger.debug(f"No handler for PGN {msg.PGN}")

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Parse NMEA2000 JSON from STDIN and publish to Keelson/Zenoh",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Add common Zenoh arguments from scaffolding
    add_common_arguments(parser)

    # Required arguments
    parser.add_argument(
        "-r",
        "--realm",
        required=True,
        help="Keelson realm (e.g., 'vessel/sv_colibri')",
    )
    parser.add_argument(
        "-e",
        "--entity-id",
        required=True,
        help="Entity identifier (e.g., 'sensors')",
    )
    parser.add_argument(
        "-s",
        "--source-id",
        required=True,
        help="Source identifier (e.g., 'n2k/primary')",
    )

    # Optional arguments
    parser.add_argument(
        "--publish-raw",
        action="store_true",
        help="Also publish raw JSON messages to 'raw' subject",
    )

    args = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=args.log_level)

    logger.info("Starting n2k2keelson")
    logger.info(f"Realm: {args.realm}")
    logger.info(f"Entity ID: {args.entity_id}")
    logger.info(f"Source ID: {args.source_id}")
    logger.info(f"Supported PGNs: {sorted(PGN_HANDLERS.keys())}")

    # Configure Zenoh using scaffolding
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    # Open Zenoh session
    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)
    logger.info("Zenoh session opened")

    try:
        # Read from stdin line by line
        logger.info("Reading JSON from STDIN...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            process_message(
                line,
                session,
                args.realm,
                args.entity_id,
                args.source_id,
                args.publish_raw,
            )

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Closing Zenoh session...")
        session.close()
        logger.info("Session closed")


if __name__ == "__main__":
    main()
