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
- MWV: Wind Speed and Angle
- VWR: Relative (Apparent) Wind Speed and Angle
- VWT: True Wind Speed and Angle
- MWD: Wind Direction and Speed
- DPT: Depth of Water
- DBT: Depth Below Transducer
- DBS: Depth Below Surface
- MTW: Mean Temperature of Water
- VBW: Dual Ground/Water Speed (longitudinal water speed only)
- GSV: Satellites in View
- XDR: Transducer Measurements (air temperature, barometric pressure,
  humidity, pitch/roll/yaw)

Proprietary sentence support:
- UNIHEADINGA: Unicore dual-antenna heading (NovAtel OEM7-compatible)

NMEA 2000 PGNs encapsulated in NMEA 0183 ($MXPGN, $PCDIN), e.g. from a
Yacht Devices YDEN-02, are decoded with the nmea2000 library and published by
the PGN handlers shared with n2k2keelson (n2k_handlers.py).
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
    declare_liveliness,
    declare_publisher,
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
from nmea2000.decoder import NMEA2000Decoder
from nmea2000.input_formats import N2KFormat

# Sibling library module in this bin/ directory (an entry point such as
# n2k2keelson cannot be imported: the Docker image strips its .py extension).
import n2k_handlers

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
    sentence_type=None,
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
        sentence_type=sentence_type,
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
        PUBLISHERS[key] = declare_publisher(session, key_expr)
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
                    LocationFixQuality.RTK_STATUS_NONE,
                ),
            )
            publish_location_fix_quality(
                session,
                args,
                timestamp,
                fix_type,
                pos_type,
                rtk_status,
                sentence_type=msg.sentence_type,
            )

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


KNOTS_TO_MPS = 0.514444

# NMEA 0183 wind speed unit letter → factor to m/s
WIND_SPEED_TO_MPS = {"M": 1.0, "N": KNOTS_TO_MPS, "K": 1 / 3.6, "S": 0.44704}


def publish_float(
    session, args, subject, value, sentence_type, reference_frame=None, scale=1.0
):
    """Publish a TimestampedFloat, skipping empty or non-numeric fields."""
    if value is None or value == "":
        return
    try:
        number = float(value) * scale
    except (ValueError, TypeError):
        logger.debug(f"Invalid {subject} value: {value!r}")
        return
    publish_data(
        session,
        args.realm,
        args.entity_id,
        subject,
        enclose_from_float(number),
        args.source_id,
        sentence_type=sentence_type,
        reference_frame=reference_frame,
    )


def handle_mwv(msg, session, args):
    """
    Handle MWV - Wind Speed and Angle.

    Publishes, for reference R (relative):
    - apparent_wind_angle_deg (TimestampedFloat)
    - apparent_wind_speed_mps (TimestampedFloat)
    and for reference T (theoretical):
    - true_wind_angle_deg (TimestampedFloat)
    - true_wind_speed_mps (TimestampedFloat)
    """
    if msg.status == "V":
        return

    if msg.reference == "R":
        prefix = "apparent"
    elif msg.reference == "T":
        prefix = "true"
    else:
        logger.debug(f"Invalid MWV reference: {msg.reference}")
        return

    publish_float(
        session, args, f"{prefix}_wind_angle_deg", msg.wind_angle, msg.sentence_type
    )

    scale = WIND_SPEED_TO_MPS.get(msg.wind_speed_units)
    if scale is None:
        logger.debug(f"Invalid MWV wind speed unit: {msg.wind_speed_units}")
        return
    publish_float(
        session,
        args,
        f"{prefix}_wind_speed_mps",
        msg.wind_speed,
        msg.sentence_type,
        scale=scale,
    )


def _publish_relative_wind(msg, session, args, prefix, angle, side, speeds):
    """
    Publish a VWR/VWT style wind reading.

    The angle is given 0-180 off the bow with an L/R side; it is published as
    0-360 clockwise from the bow, matching MWV and NMEA 2000 PGN 130306.
    `speeds` is a sequence of (value, unit) tried in order until one is set.
    """
    if angle is not None and angle != "":
        try:
            value = float(angle)
            if side == "L":
                value = (360.0 - value) % 360.0
            publish_float(
                session, args, f"{prefix}_wind_angle_deg", value, msg.sentence_type
            )
        except (ValueError, TypeError):
            logger.debug(f"Invalid {msg.sentence_type} wind angle: {angle}")

    for speed, unit in speeds:
        if speed is not None and speed != "":
            publish_float(
                session,
                args,
                f"{prefix}_wind_speed_mps",
                speed,
                msg.sentence_type,
                scale=WIND_SPEED_TO_MPS[unit],
            )
            break


def handle_vwr(msg, session, args):
    """
    Handle VWR - Relative (Apparent) Wind Speed and Angle.

    Publishes:
    - apparent_wind_angle_deg (TimestampedFloat)
    - apparent_wind_speed_mps (TimestampedFloat)
    """
    _publish_relative_wind(
        msg,
        session,
        args,
        "apparent",
        msg.deg_r,
        msg.l_r,
        ((msg.wind_speed_ms, "M"), (msg.wind_speed_kn, "N"), (msg.wind_speed_km, "K")),
    )


def handle_vwt(msg, session, args):
    """
    Handle VWT - True Wind Speed and Angle.

    Publishes:
    - true_wind_angle_deg (TimestampedFloat)
    - true_wind_speed_mps (TimestampedFloat)
    """
    _publish_relative_wind(
        msg,
        session,
        args,
        "true",
        msg.wind_angle_vessel,
        msg.direction,
        (
            (msg.wind_speed_meters, "M"),
            (msg.wind_speed_knots, "N"),
            (msg.wind_speed_km, "K"),
        ),
    )


def handle_mwd(msg, session, args):
    """
    Handle MWD - Wind Direction and Speed.

    Publishes:
    - true_wind_direction_deg (TimestampedFloat)
      Magnetic direction publishes to the same subject with `/magnetic`
      appended to the source_id.
    - true_wind_speed_mps (TimestampedFloat) - from m/s or knots
    """
    publish_float(
        session, args, "true_wind_direction_deg", msg.direction_true, msg.sentence_type
    )
    publish_float(
        session,
        args,
        "true_wind_direction_deg",
        msg.direction_magnetic,
        msg.sentence_type,
        reference_frame="magnetic",
    )

    if msg.wind_speed_meters is not None and msg.wind_speed_meters != "":
        publish_float(
            session,
            args,
            "true_wind_speed_mps",
            msg.wind_speed_meters,
            msg.sentence_type,
        )
    else:
        publish_float(
            session,
            args,
            "true_wind_speed_mps",
            msg.wind_speed_knots,
            msg.sentence_type,
            scale=KNOTS_TO_MPS,
        )


def handle_dpt(msg, session, args):
    """
    Handle DPT - Depth of Water.

    Publishes:
    - depth_below_transducer_m (TimestampedFloat)
    - depth_below_surface_m (TimestampedFloat) - when offset is positive
    - depth_below_keel_m (TimestampedFloat) - when offset is negative
    """
    if msg.depth is None or msg.depth == "":
        return
    try:
        depth = float(msg.depth)
    except (ValueError, TypeError):
        logger.debug(f"Invalid DPT depth: {msg.depth}")
        return

    publish_float(session, args, "depth_below_transducer_m", depth, msg.sentence_type)

    try:
        offset = float(msg.offset) if msg.offset not in (None, "") else 0.0
    except (ValueError, TypeError):
        logger.debug(f"Invalid DPT offset: {msg.offset}")
        return

    # Positive offset: transducer to waterline. Negative: transducer to keel.
    if offset > 0:
        publish_float(
            session, args, "depth_below_surface_m", depth + offset, msg.sentence_type
        )
    elif offset < 0:
        publish_float(
            session, args, "depth_below_keel_m", depth + offset, msg.sentence_type
        )


def handle_dbt(msg, session, args):
    """
    Handle DBT - Depth Below Transducer.

    Publishes:
    - depth_below_transducer_m (TimestampedFloat)
    """
    publish_float(
        session, args, "depth_below_transducer_m", msg.depth_meters, msg.sentence_type
    )


def handle_dbs(msg, session, args):
    """
    Handle DBS - Depth Below Surface.

    Publishes:
    - depth_below_surface_m (TimestampedFloat)
    """
    publish_float(
        session, args, "depth_below_surface_m", msg.depth_meter, msg.sentence_type
    )


def handle_mtw(msg, session, args):
    """
    Handle MTW - Mean Temperature of Water.

    Publishes:
    - water_temperature_celsius (TimestampedFloat)
    """
    if msg.units not in (None, "", "C"):
        logger.debug(f"Unsupported MTW unit: {msg.units}")
        return
    publish_float(
        session, args, "water_temperature_celsius", msg.temperature, msg.sentence_type
    )


def handle_vbw(msg, session, args):
    """
    Handle VBW - Dual Ground/Water Speed.

    Publishes:
    - speed_through_water_knots (TimestampedFloat) - longitudinal water speed

    Ground speed components are along/across the hull, not over-ground speed
    along the track, so they are not published as speed_over_ground_knots.
    """
    if msg.data_validity_water_spd != "A":
        return
    publish_float(
        session, args, "speed_through_water_knots", msg.lon_water_spd, msg.sentence_type
    )


def handle_gsv(msg, session, args):
    """
    Handle GSV - Satellites in View.

    Publishes (once per GSV group, from its first message):
    - location_fix_satellites_visible (TimestampedInt)

    A multi-constellation receiver sends one group per talker ($GPGSV,
    $GLGSV, ...), each with its own count, so the talker is appended to the
    source_id (`.../GSV/gp`, `.../GSV/gl`) to keep them distinct series.
    """
    if str(msg.msg_num) != "1" or not msg.num_sv_in_view:
        return
    try:
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "location_fix_satellites_visible",
            enclose_from_integer(int(msg.num_sv_in_view)),
            args.source_id,
            sentence_type=f"{msg.sentence_type}/{msg.talker.lower()}",
        )
    except (ValueError, TypeError):
        logger.debug(f"Invalid satellites in view: {msg.num_sv_in_view}")


XDR_ANGLE_SUBJECTS = {"pitch": "pitch_deg", "roll": "roll_deg", "yaw": "yaw_deg"}
XDR_PRESSURE_TO_PA = {"P": 1.0, "B": 100000.0}


def handle_xdr(msg, session, args):
    """
    Handle XDR - Transducer Measurements.

    Each transducer is a (type, value, units, name) quadruplet. Mapped:
    - A,<v>,D,Pitch|Roll|Yaw → pitch_deg / roll_deg / yaw_deg
    - C,<v>,C,<name with "air"|"water"> → air_/water_temperature_celsius
    - P,<v>,P|B,<name with "baro"> → air_pressure_pa
    - H,<v>,P,<name> → air_relative_humidity_pct

    Other transducers are logged and ignored.
    """
    for index in range(msg.num_transducers):
        transducer = msg.get_transducer(index)
        name = (transducer.id or "").lower()
        subject = None
        scale = 1.0

        if transducer.type == "A" and transducer.units == "D":
            subject = XDR_ANGLE_SUBJECTS.get(name)
        elif transducer.type == "C" and transducer.units == "C":
            if "water" in name:
                subject = "water_temperature_celsius"
            elif "air" in name:
                subject = "air_temperature_celsius"
        elif (
            transducer.type == "P"
            and transducer.units in XDR_PRESSURE_TO_PA
            and "baro" in name
        ):
            subject = "air_pressure_pa"
            scale = XDR_PRESSURE_TO_PA[transducer.units]
        elif transducer.type == "H":
            subject = "air_relative_humidity_pct"

        if subject is None:
            logger.debug(f"Unmapped XDR transducer: {transducer}")
            continue

        publish_float(
            session, args, subject, transducer.value, msg.sentence_type, scale=scale
        )


# --- NMEA 2000 PGNs encapsulated in NMEA 0183 ---------------------------

N2K_SENTENCE_PREFIXES = ("$MXPGN,", "$PCDIN,")

# One decoder per sentence type: an NMEA2000Decoder binds to a single format
N2K_DECODERS: Dict[str, NMEA2000Decoder] = {}

UNHANDLED_PGNS: set = set()  # PGNs already logged as unhandled


def nmea_checksum_ok(line):
    """Verify the XOR checksum of a `$...*hh` sentence."""
    body, separator, checksum = line[1:].partition("*")
    if not separator:
        return False
    try:
        expected = int(checksum[:2], 16)
    except ValueError:
        return False
    actual = 0
    for char in body:
        actual ^= ord(char)
    return actual == expected


def decode_n2k_sentence(line, mxpgn_byte_order="forward"):
    """
    Decode an NMEA 2000 PGN encapsulated in an NMEA 0183 sentence.

    - $PCDIN,<pgn>,<timestamp>,<source>,<data>*hh  (SeaSmart.Net)
    - $MXPGN,<pgn>,<attribute>,<data>*hh  (Shipmodul MiniPlex, Yacht Devices)

    The nmea2000 decoder expects $MXPGN data bytes reversed, as a MiniPlex
    sends them. A YDEN-02 sends them in transmission order (the same bytes as
    its $PCDIN), so with mxpgn_byte_order "forward" they are reversed first.

    The message timestamp is set to the time of reception, because the $PCDIN
    timestamp field counts from device start-up.

    Returns an NMEA2000Message, or None when the decoder does not know the PGN.
    Raises ValueError on a bad checksum or malformed input.
    """
    if not nmea_checksum_ok(line):
        raise ValueError(f"Checksum mismatch: {line!r}")

    sentence_type = line[1:6]
    if sentence_type == "MXPGN" and mxpgn_byte_order == "forward":
        fields = line.partition("*")[0].split(",")
        if len(fields) != 4:
            raise ValueError(f"Invalid MXPGN sentence: {line!r}")
        fields[3] = bytes.fromhex(fields[3])[::-1].hex().upper()
        line = ",".join(fields)  # The decoder does not verify the checksum

    decoder = N2K_DECODERS.get(sentence_type)
    if decoder is None:
        decoder = N2K_DECODERS[sentence_type] = NMEA2000Decoder(
            bound_format=N2KFormat(sentence_type.lower())
        )

    msg = decoder.decode(line)
    if msg is not None:
        msg.timestamp = datetime.now(timezone.utc)
    return msg


def handle_n2k_sentence(line, session, args):
    """
    Decode an encapsulated PGN and dispatch it to the shared PGN handlers.

    Publishes under source_id `<source_id>/<mxpgn|pcdin>/<source address>`, to
    which the handlers append any instance chunks.

    Returns True when a handler ran.
    """
    msg = decode_n2k_sentence(line, args.mxpgn_byte_order)
    if msg is None:
        return False

    handler = n2k_handlers.PGN_HANDLERS.get(msg.PGN)
    if handler is None:
        if msg.PGN not in UNHANDLED_PGNS:
            UNHANDLED_PGNS.add(msg.PGN)
            logger.debug(f"No handler for PGN {msg.PGN} ({msg.id})")
        return False

    source_id = f"{args.source_id}/{line[1:6].lower()}/{msg.source}"
    handler(msg, session, args.realm, args.entity_id, source_id)
    return True


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
    "MWV": handle_mwv,
    "VWR": handle_vwr,
    "VWT": handle_vwt,
    "MWD": handle_mwd,
    "DPT": handle_dpt,
    "DBT": handle_dbt,
    "DBS": handle_dbs,
    "MTW": handle_mtw,
    "VBW": handle_vbw,
    "GSV": handle_gsv,
    "XDR": handle_xdr,
}

# Static, parser-supported subject vocabulary — every subject any handler in
# MESSAGE_HANDLERS (plus the UNIHEADINGA fast-path, and the shared PGN
# handlers reached through $MXPGN / $PCDIN) can possibly publish,
# regardless of which sentence types the physical NMEA install actually
# emits. Declared unconditionally per capability semantics. Kept in sync
# manually with the `publish_data(..., subject, ...)` call sites above.
NMEA0183_SUPPORTED_SUBJECTS = (
    "location_fix",
    "location_fix_quality",
    "location_fix_satellites_used",
    "location_fix_hdop",
    "location_fix_vdop",
    "location_fix_pdop",
    "location_fix_undulation_m",
    "speed_over_ground_knots",
    "course_over_ground_deg",
    "heading_true_north_deg",
    "heading_magnetic_deg",
    "timestamp",
    "yaw_rate_degps",
    "magnetic_deviation_deg",
    "magnetic_variation_deg",
    "air_pressure_pa",
    "air_temperature_celsius",
    "water_temperature_celsius",
    "air_relative_humidity_pct",
    "dew_point_celsius",
    "true_wind_direction_deg",
    "true_wind_speed_mps",
    "true_wind_angle_deg",
    "apparent_wind_angle_deg",
    "apparent_wind_speed_mps",
    "depth_below_transducer_m",
    "depth_below_surface_m",
    "depth_below_keel_m",
    "speed_through_water_knots",
    "location_fix_satellites_visible",
    "pitch_deg",
    "roll_deg",
    "yaw_deg",
)
NMEA0183_SUPPORTED_SUBJECTS = tuple(
    dict.fromkeys(NMEA0183_SUPPORTED_SUBJECTS + n2k_handlers.N2K_SUPPORTED_SUBJECTS)
)


def process_line(line, session, args, line_number=0):
    """
    Process one line of input.

    With --publish-raw every non-empty line is published on raw_nmea0183,
    including sentences no handler understands.

    Returns True when a handler consumed the line.
    """
    line = line.strip()
    if not line:
        return False

    if args.publish_raw:
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "raw_nmea0183",
            enclose_from_string(line),
            args.source_id,
        )

    if line.startswith("#UNIHEADINGA"):
        try:
            handle_uniheadinga(parse_uniheadinga(line), session, args)
            return True
        except Exception as e:
            logger.debug(f"UNIHEADINGA parse error on line {line_number}: {e}")
            return False

    if line.startswith(N2K_SENTENCE_PREFIXES):
        try:
            return handle_n2k_sentence(line, session, args)
        except Exception as e:
            logger.debug(f"NMEA 2000 sentence error on line {line_number}: {e}")
            return False

    if not line.startswith("$"):
        return False

    try:
        msg = pynmea2.parse(line)
    except pynmea2.ParseError as e:
        logger.debug(f"Parse error on line {line_number}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error parsing line {line_number}: {e!r} ({line!r})")
        return False

    # Proprietary and query sentences have no sentence_type
    sentence_type = getattr(msg, "sentence_type", None)
    handler = MESSAGE_HANDLERS.get(sentence_type)
    if handler is None:
        logger.debug(f"No handler for {sentence_type or type(msg).__name__}: {line}")
        return False

    logger.debug(f"Parsed {sentence_type}: {line}")
    try:
        handler(msg, session, args)
    except Exception as e:
        logger.error(f"Error processing line {line_number}: {e!r} ({line!r})")
        return False
    return True


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
    parser.add_argument(
        "--mxpgn-byte-order",
        choices=("forward", "reversed"),
        default="forward",
        help="Data byte order of $MXPGN sentences: 'forward' as a Yacht Devices "
        "YDEN-02 sends them, 'reversed' as a Shipmodul MiniPlex does",
    )

    args = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=args.log_level)

    # Configure Zenoh using scaffolding
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    # Initialize Zenoh logging
    zenoh.init_log_from_env_or(logging.getLevelName(args.log_level))

    pubsub_subjects = list(NMEA0183_SUPPORTED_SUBJECTS)
    if args.publish_raw:
        pubsub_subjects.append("raw_nmea0183")

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        with declare_liveliness(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            pubsub_subjects=pubsub_subjects,
        ):
            logger.info(f"Connected to realm: {args.realm}, entity: {args.entity_id}")
            logger.info(f"Publishing with source_id: {args.source_id}")
            logger.info(f"Supported NMEA types: {', '.join(MESSAGE_HANDLERS.keys())}")
            logger.info(
                "Supported encapsulated PGNs: "
                f"{', '.join(str(pgn) for pgn in sorted(n2k_handlers.PGN_HANDLERS))}"
            )
            logger.info("Reading NMEA sentences from STDIN...")

            line_count = 0
            parsed_count = 0

            try:
                for line in sys.stdin:
                    line_count += 1
                    if process_line(line, session, args, line_count):
                        parsed_count += 1

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                logger.info(
                    f"Processed {line_count} lines, "
                    f"parsed {parsed_count} supported messages"
                )


if __name__ == "__main__":
    main()
