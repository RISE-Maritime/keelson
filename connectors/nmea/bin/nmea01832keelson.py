#!/usr/bin/env python3

"""
Command line utility for parsing NMEA0183 sentences from STDIN and publishing to Keelson/Zenoh.

Reads NMEA sentences line-by-line from standard input, parses them using pynmea2,
and publishes the extracted data to appropriate Keelson subjects on the Zenoh bus.

Supported NMEA sentence types:
- GGA: Global Positioning System Fix Data
- RMC: Recommended Minimum Specific GNSS Data
- HDT: Heading True
- HDG: Heading, Deviation and Variation
- HDM: Heading, Magnetic
- VTG: Track Made Good and Ground Speed
- ZDA: Date and Time
- GLL: Geographic Position Latitude/Longitude
- ROT: Rate of Turn
- GSA: GNSS DOP and Active Satellites
- MDA: Meteorological Composite

Proprietary sentence support:
- UNIHEADINGA: Unicore dual-antenna heading (NovAtel OEM7-compatible)
"""

import sys
import time
import logging
import argparse
from datetime import datetime, timezone
from typing import Dict, Any

import zenoh
import pynmea2
import keelson
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
)
from keelson.helpers import (
    enclose_from_float,
    enclose_from_integer,
    enclose_from_lon_lat,
    enclose_from_string,
    enclose_from_timestamp,
)
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

# Global state
PUBLISHERS: Dict[tuple, Any] = {}  # Cache for lazy publisher creation

logger = logging.getLogger("nmea01832keelson")

# Map GGA quality indicator (field 6) → (FixType, PosType, RtkStatus).
# Integrity is not carried by GGA; left as INTEGRITY_UNKNOWN at the call site.
GGA_QUALITY_MAP: Dict[int, tuple] = {
    0: (
        LocationFixQuality.INVALID,
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
        LocationFixQuality.POS_TYPE_SINGLE,
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


def publish_location_fix_quality(
    session,
    args,
    timestamp_ns,
    fix_type=LocationFixQuality.UNKNOWN,
    pos_type=LocationFixQuality.POS_TYPE_UNKNOWN,
    rtk_status=LocationFixQuality.RTK_STATUS_UNKNOWN,
    integrity=LocationFixQuality.INTEGRITY_UNKNOWN,
):
    """Build a LocationFixQuality message and publish it on location_fix_quality."""
    payload = LocationFixQuality()
    payload.timestamp.FromNanoseconds(timestamp_ns or time.time_ns())
    payload.fix_type = fix_type
    payload.pos_type = pos_type
    payload.rtk_status = rtk_status
    payload.integrity = integrity
    publish_data(
        session,
        args.realm,
        args.entity_id,
        "location_fix_quality",
        keelson.enclose(payload.SerializeToString()),
        args.source_id,
    )


def get_or_create_publisher(
    session, realm: str, entity_id: str, subject: str, source_id: str
):
    """
    Get or create a Zenoh publisher for the specified subject.

    Publishers are cached globally to avoid recreating them for repeated publishes.
    """
    key = (realm, entity_id, subject, source_id)
    if key not in PUBLISHERS:
        key_expr = keelson.construct_pubsub_key(realm, entity_id, subject, source_id)
        PUBLISHERS[key] = session.declare_publisher(key_expr)
        logger.debug(f"Created publisher for {key_expr}")
    return PUBLISHERS[key]


def publish_data(
    session,
    realm: str,
    entity_id: str,
    subject: str,
    value: bytes,
    source_id: str,
    sentence_type: str = None,
    reference_frame: str = None,
):
    """Publish data to a Keelson subject."""
    parts = [source_id]
    if sentence_type:
        parts.append(sentence_type)
    if reference_frame:
        parts.append(reference_frame)
    effective_source_id = "/".join(parts)
    publisher = get_or_create_publisher(
        session, realm, entity_id, subject, effective_source_id
    )
    publisher.put(value)


def nmea_time_to_nanoseconds(date_obj, time_obj) -> int:
    """
    Convert NMEA date and time objects to nanoseconds since epoch.

    Args:
        date_obj: datetime.date from NMEA sentence
        time_obj: datetime.time from NMEA sentence

    Returns:
        Nanoseconds since epoch (int), or None
    """
    if date_obj and time_obj:
        dt = datetime.combine(date_obj, time_obj, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    elif time_obj:
        # Use today's date if no date provided
        today = datetime.now(timezone.utc).date()
        dt = datetime.combine(today, time_obj, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    return None


def handle_gga(msg, session, args):
    """
    Handle GGA - Global Positioning System Fix Data.

    Publishes:
    - location_fix (LocationFix)
    - location_fix_quality (LocationFixQuality)
    - location_fix_satellites_used (TimestampedInt)
    - location_fix_hdop (TimestampedFloat)
    - location_fix_undulation_m (TimestampedFloat)
    """
    timestamp = nmea_time_to_nanoseconds(None, msg.timestamp)

    # Publish fix quality (GGA field 6 mapped to LocationFixQuality enum)
    if msg.gps_qual is not None:
        try:
            fix_type = GGA_QUALITY_TO_FIX_TYPE.get(
                int(msg.gps_qual), LocationFixQuality.UNKNOWN
            )
            payload = LocationFixQuality()
            payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
            payload.fix_type = fix_type
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_quality",
                keelson.enclose(payload.SerializeToString()),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid gps_qual value: {msg.gps_qual}")

    # Publish location fix if position is valid (with altitude when available)
    if msg.latitude and msg.longitude:
        loc = LocationFix()
        loc.timestamp.FromNanoseconds(timestamp or time.time_ns())
        loc.latitude = msg.latitude
        loc.longitude = msg.longitude
        if msg.altitude:
            try:
                loc.altitude = float(msg.altitude)
            except (ValueError, TypeError):
                pass
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "location_fix",
            keelson.enclose(loc.SerializeToString()),
            args.source_id,
            sentence_type=msg.sentence_type,
        )

    # Publish number of satellites used
    if msg.num_sats:
        try:
            num_sats = int(msg.num_sats)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_satellites_used",
                enclose_from_integer(num_sats, timestamp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid num_sats value: {msg.num_sats}")

    # Publish HDOP
    if msg.horizontal_dil:
        try:
            hdop = float(msg.horizontal_dil)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_hdop",
                enclose_from_float(hdop, timestamp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid HDOP value: {msg.horizontal_dil}")

    # Publish geoid undulation
    if msg.geo_sep:
        try:
            undulation = float(msg.geo_sep)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_undulation_m",
                enclose_from_float(undulation, timestamp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid geoid separation value: {msg.geo_sep}")

    # Publish fix quality derived from the GGA quality indicator (field 6)
    if msg.gps_qual is not None:
        try:
            qual = int(msg.gps_qual)
        except (ValueError, TypeError):
            logger.debug(f"Invalid gps_qual value: {msg.gps_qual}")
        else:
            fix_type, pos_type, rtk_status = GGA_QUALITY_MAP.get(
                qual,
                (
                    LocationFixQuality.UNKNOWN,
                    LocationFixQuality.POS_TYPE_UNKNOWN,
                    LocationFixQuality.RTK_STATUS_UNKNOWN,
                ),
            )
            publish_location_fix_quality(
                session, args, timestamp, fix_type, pos_type, rtk_status
            )


def handle_rmc(msg, session, args):
    """
    Handle RMC - Recommended Minimum Specific GNSS Data.

    Publishes:
    - location_fix (LocationFix)
    - speed_over_ground_knots (TimestampedFloat)
    - course_over_ground_deg (TimestampedFloat)
    """
    timestamp = nmea_time_to_nanoseconds(msg.datestamp, msg.timestamp)

    # Publish location fix if position is valid and status is active
    if msg.status == "A" and msg.latitude and msg.longitude:
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "location_fix",
            enclose_from_lon_lat(msg.longitude, msg.latitude, timestamp),
            args.source_id,
            sentence_type=msg.sentence_type,
        )

    # Publish speed over ground
    if msg.spd_over_grnd is not None:
        try:
            speed = float(msg.spd_over_grnd)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "speed_over_ground_knots",
                enclose_from_float(speed, timestamp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid speed value: {msg.spd_over_grnd}")

    # Publish course over ground
    if msg.true_course is not None:
        try:
            course = float(msg.true_course)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "course_over_ground_deg",
                enclose_from_float(course, timestamp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid course value: {msg.true_course}")


def handle_hdt(msg, session, args):
    """
    Handle HDT - Heading True.

    Publishes:
    - heading_true_north_deg (TimestampedFloat)
    """
    if msg.heading is not None:
        try:
            heading = float(msg.heading)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "heading_true_north_deg",
                enclose_from_float(heading),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid heading value: {msg.heading}")


def handle_vtg(msg, session, args):
    """
    Handle VTG - Track Made Good and Ground Speed.

    Publishes:
    - course_over_ground_deg (TimestampedFloat)
    - speed_over_ground_knots (TimestampedFloat)
    """
    # Publish true course
    if msg.true_track is not None:
        try:
            course = float(msg.true_track)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "course_over_ground_deg",
                enclose_from_float(course),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid true track value: {msg.true_track}")

    # Publish speed in knots
    if msg.spd_over_grnd_kts is not None:
        try:
            speed = float(msg.spd_over_grnd_kts)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "speed_over_ground_knots",
                enclose_from_float(speed),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid speed value: {msg.spd_over_grnd_kts}")


def handle_zda(msg, session, args):
    """
    Handle ZDA - Date and Time.

    Publishes:
    - timestamp (TimestampedTimestamp)
    """
    if msg.timestamp and msg.day and msg.month and msg.year:
        try:
            dt = datetime(
                int(msg.year),
                int(msg.month),
                int(msg.day),
                msg.timestamp.hour,
                msg.timestamp.minute,
                msg.timestamp.second,
                msg.timestamp.microsecond,
                tzinfo=timezone.utc,
            )
            timestamp_ns = int(dt.timestamp() * 1_000_000_000)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "timestamp",
                enclose_from_timestamp(timestamp_ns),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError) as e:
            logger.debug(f"Invalid ZDA timestamp: {e}")


def handle_gll(msg, session, args):
    """
    Handle GLL - Geographic Position Latitude/Longitude.

    Publishes:
    - location_fix (LocationFix)
    """
    timestamp = nmea_time_to_nanoseconds(None, msg.timestamp)

    # Only publish if status is valid
    if msg.status == "A" and msg.latitude and msg.longitude:
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "location_fix",
            enclose_from_lon_lat(msg.longitude, msg.latitude, timestamp),
            args.source_id,
            sentence_type=msg.sentence_type,
        )


def handle_rot(msg, session, args):
    """
    Handle ROT - Rate of Turn.

    Publishes:
    - yaw_rate_degps (TimestampedFloat)

    Note: NMEA ROT uses degrees per minute, Keelson uses degrees per second.
    """
    if msg.rate_of_turn is not None:
        try:
            rot_deg_per_min = float(msg.rate_of_turn)
            # Convert from degrees per minute to degrees per second
            rot_deg_per_sec = rot_deg_per_min / 60.0
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "yaw_rate_degps",
                enclose_from_float(rot_deg_per_sec),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid ROT value: {msg.rate_of_turn}")


def handle_gsa(msg, session, args):
    """
    Handle GSA - GNSS DOP and Active Satellites.

    Publishes:
    - location_fix_hdop (TimestampedFloat)
    - location_fix_vdop (TimestampedFloat)
    - location_fix_pdop (TimestampedFloat)
    """
    # Publish HDOP
    if msg.hdop:
        try:
            hdop = float(msg.hdop)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_hdop",
                enclose_from_float(hdop),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid HDOP value: {msg.hdop}")

    # Publish VDOP
    if msg.vdop:
        try:
            vdop = float(msg.vdop)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_vdop",
                enclose_from_float(vdop),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid VDOP value: {msg.vdop}")

    # Publish PDOP
    if msg.pdop:
        try:
            pdop = float(msg.pdop)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "location_fix_pdop",
                enclose_from_float(pdop),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid PDOP value: {msg.pdop}")


def handle_hdg(msg, session, args):
    """
    Handle HDG - Heading, Deviation and Variation.

    Publishes:
    - heading_magnetic_deg (TimestampedFloat)
    - magnetic_deviation_deg (TimestampedFloat) - if available
    - magnetic_variation_deg (TimestampedFloat) - if available
    """
    # Publish magnetic heading
    if msg.heading is not None:
        try:
            heading = float(msg.heading)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "heading_magnetic_deg",
                enclose_from_float(heading),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid heading value: {msg.heading}")

    # Publish magnetic deviation (E = positive, W = negative)
    if msg.deviation is not None:
        try:
            deviation = float(msg.deviation)
            if msg.dev_dir == "W":
                deviation = -deviation
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "magnetic_deviation_deg",
                enclose_from_float(deviation),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid deviation value: {msg.deviation}")

    # Publish magnetic variation (E = positive, W = negative)
    if msg.variation is not None:
        try:
            variation = float(msg.variation)
            if msg.var_dir == "W":
                variation = -variation
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "magnetic_variation_deg",
                enclose_from_float(variation),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid variation value: {msg.variation}")


def handle_hdm(msg, session, args):
    """
    Handle HDM - Heading, Magnetic.

    Publishes:
    - heading_magnetic_deg (TimestampedFloat)
    """
    if msg.heading is not None:
        try:
            heading = float(msg.heading)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "heading_magnetic_deg",
                enclose_from_float(heading),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid heading value: {msg.heading}")


def handle_mda(msg, session, args):
    """
    Handle MDA - Meteorological Composite.

    Publishes:
    - air_pressure_pa (TimestampedFloat) - from bars or inches Hg
    - air_temperature_celsius (TimestampedFloat)
    - water_temperature_celsius (TimestampedFloat)
    - relative_humidity_percent (TimestampedFloat)
    - dew_point_celsius (TimestampedFloat)
    - true_wind_direction_deg (TimestampedFloat)
      Magnetic direction publishes to the same subject with `/magnetic`
      appended to the source_id.
    - true_wind_speed_mps (TimestampedFloat) - from m/s or knots
    """
    # Publish air pressure (convert from bars or inches Hg to Pascals)
    if hasattr(msg, "b_pressure_bar") and msg.b_pressure_bar is not None:
        try:
            pressure_pa = float(msg.b_pressure_bar) * 100000.0
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "air_pressure_pa",
                enclose_from_float(pressure_pa),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid bar pressure value: {msg.b_pressure_bar}")
    elif hasattr(msg, "i_pressure_inch") and msg.i_pressure_inch is not None:
        try:
            pressure_pa = float(msg.i_pressure_inch) * 3386.39
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "air_pressure_pa",
                enclose_from_float(pressure_pa),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid inch pressure value: {msg.i_pressure_inch}")

    # Publish air temperature
    if hasattr(msg, "air_temp") and msg.air_temp is not None:
        try:
            air_temp = float(msg.air_temp)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "air_temperature_celsius",
                enclose_from_float(air_temp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid air temp value: {msg.air_temp}")

    # Publish water temperature
    if hasattr(msg, "water_temp") and msg.water_temp is not None:
        try:
            water_temp = float(msg.water_temp)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "water_temperature_celsius",
                enclose_from_float(water_temp),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid water temp value: {msg.water_temp}")

    # Publish relative humidity
    if hasattr(msg, "rel_humidity") and msg.rel_humidity is not None:
        try:
            humidity = float(msg.rel_humidity)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "air_relative_humidity_pct",
                enclose_from_float(humidity),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid humidity value: {msg.rel_humidity}")

    # Publish dew point
    if hasattr(msg, "dew_point") and msg.dew_point is not None:
        try:
            dew_point = float(msg.dew_point)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "dew_point_celsius",
                enclose_from_float(dew_point),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid dew point value: {msg.dew_point}")

    # Publish true wind direction
    if hasattr(msg, "direction_true") and msg.direction_true is not None:
        try:
            wind_dir_true = float(msg.direction_true)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "true_wind_direction_deg",
                enclose_from_float(wind_dir_true),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid true wind direction: {msg.direction_true}")

    # Publish magnetic wind direction (same subject as true, distinguished by source_id)
    if hasattr(msg, "direction_magnetic") and msg.direction_magnetic is not None:
        try:
            wind_dir_mag = float(msg.direction_magnetic)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "true_wind_direction_deg",
                enclose_from_float(wind_dir_mag),
                args.source_id,
                sentence_type=msg.sentence_type,
                reference_frame="magnetic",
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid magnetic wind direction: {msg.direction_magnetic}")

    # Publish wind speed (convert to m/s if needed)
    if hasattr(msg, "wind_speed_meters") and msg.wind_speed_meters is not None:
        try:
            wind_speed = float(msg.wind_speed_meters)
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "true_wind_speed_mps",
                enclose_from_float(wind_speed),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid wind speed m/s: {msg.wind_speed_meters}")
    elif hasattr(msg, "wind_speed_knots") and msg.wind_speed_knots is not None:
        try:
            wind_speed = float(msg.wind_speed_knots) * 0.514444
            publish_data(
                session,
                args.realm,
                args.entity_id,
                "true_wind_speed_mps",
                enclose_from_float(wind_speed),
                args.source_id,
                sentence_type=msg.sentence_type,
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid wind speed knots: {msg.wind_speed_knots}")


def parse_uniheadinga(line):
    """
    Parse a proprietary #UNIHEADINGA sentence (Unicore/NovAtel OEM7 format).

    Format: #UNIHEADINGA,header_fields...;body_fields...*crc32
    Body fields: solution_status,position_type,baseline_length,heading,pitch,...

    Returns a dict with solution_status (str), heading (float), and pitch (float).
    Raises ValueError on malformed input.
    """
    parts = line.split(";", 1)
    if len(parts) != 2:
        raise ValueError(f"Missing semicolon separator in UNIHEADINGA: {line!r}")

    body = parts[1]
    # Strip CRC32 checksum (everything after last '*')
    crc_idx = body.rfind("*")
    if crc_idx != -1:
        body = body[:crc_idx]

    fields = body.split(",")
    if len(fields) < 5:
        raise ValueError(
            f"UNIHEADINGA body has {len(fields)} fields, expected at least 5"
        )

    return {
        "solution_status": fields[0],
        "heading": float(fields[3]),
        "pitch": float(fields[4]),
    }


def handle_uniheadinga(fields, session, args):
    """
    Handle parsed UNIHEADINGA data.

    Only publishes when solution_status is SOL_COMPUTED.

    Publishes:
    - heading_true_north_deg (TimestampedFloat)
    - pitch_deg (TimestampedFloat)
    """
    if fields["solution_status"] != "SOL_COMPUTED":
        logger.debug(
            f"UNIHEADINGA skipped: solution_status={fields['solution_status']}"
        )
        return

    try:
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "heading_true_north_deg",
            enclose_from_float(fields["heading"]),
            args.source_id,
            sentence_type="UNIHEADINGA",
        )
    except (ValueError, TypeError):
        logger.debug(f"Invalid UNIHEADINGA heading: {fields['heading']}")

    try:
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "pitch_deg",
            enclose_from_float(fields["pitch"]),
            args.source_id,
            sentence_type="UNIHEADINGA",
        )
    except (ValueError, TypeError):
        logger.debug(f"Invalid UNIHEADINGA pitch: {fields['pitch']}")


# Handler registry mapping sentence types to handler functions
MESSAGE_HANDLERS = {
    "GGA": handle_gga,
    "RMC": handle_rmc,
    "HDT": handle_hdt,
    "HDG": handle_hdg,
    "HDM": handle_hdm,
    "VTG": handle_vtg,
    "ZDA": handle_zda,
    "GLL": handle_gll,
    "ROT": handle_rot,
    "GSA": handle_gsa,
    "MDA": handle_mda,
}


def main():
    parser = argparse.ArgumentParser(
        prog="nmea01832keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Parse NMEA0183 sentences from STDIN and publish to Keelson/Zenoh",
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
    parser.add_argument(
        "-s",
        "--source-id",
        type=str,
        required=True,
        help="Source identifier for published data",
    )

    # Optional features
    parser.add_argument(
        "--publish-raw",
        action="store_true",
        help="Also publish raw NMEA sentences to 'raw' subject",
    )

    args = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=args.log_level)

    # Configure Zenoh using scaffolding
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    # Initialize Zenoh logging
    zenoh.init_log_from_env_or(logging.getLevelName(args.log_level))

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            logger.info(f"Connected to realm: {args.realm}, entity: {args.entity_id}")
            logger.info(f"Publishing with source_id: {args.source_id}")
            logger.info(f"Supported NMEA types: {', '.join(MESSAGE_HANDLERS.keys())}")
            logger.info("Reading NMEA sentences from STDIN...")

            line_count = 0
            parsed_count = 0

            try:
                for line in sys.stdin:
                    line_count += 1
                    line = line.strip()

                    if not line:
                        continue

                    if line.startswith("#UNIHEADINGA"):
                        try:
                            fields = parse_uniheadinga(line)
                            handle_uniheadinga(fields, session, args)
                            parsed_count += 1
                        except Exception as e:
                            logger.debug(
                                f"UNIHEADINGA parse error on line {line_count}: {e}"
                            )
                        continue

                    if not line.startswith("$"):
                        continue

                    try:
                        msg = pynmea2.parse(line)
                        sentence_type = msg.sentence_type

                        logger.debug(f"Parsed {sentence_type}: {line}")

                        # Handle message if we have a handler for this type
                        if sentence_type in MESSAGE_HANDLERS:
                            MESSAGE_HANDLERS[sentence_type](msg, session, args)
                            parsed_count += 1
                        else:
                            logger.debug(f"No handler for {sentence_type}")

                        # Optionally publish raw NMEA
                        if args.publish_raw:
                            publish_data(
                                session,
                                args.realm,
                                args.entity_id,
                                "raw_nmea0183",
                                enclose_from_string(line),
                                args.source_id,
                            )

                    except pynmea2.ParseError as e:
                        logger.debug(f"Parse error on line {line_count}: {e}")
                    except Exception as e:
                        logger.error(f"Error processing line {line_count}: {e}")

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                logger.info(
                    f"Processed {line_count} lines, "
                    f"parsed {parsed_count} supported messages"
                )


if __name__ == "__main__":
    main()
