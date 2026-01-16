#!/usr/bin/env python3

"""
Command line utility for subscribing to Keelson/Zenoh and outputting NMEA0183 to STDOUT.

Subscribes to specified Keelson subjects on the Zenoh bus, aggregates the data using
skarv, and generates NMEA0183 sentences written to standard output.

Generated NMEA sentence types:
- GGA: Global Positioning System Fix Data
- RMC: Recommended Minimum Specific GNSS Data
- HDT: Heading True
- VTG: Track Made Good and Ground Speed
- ZDA: Date and Time
- GLL: Geographic Position Latitude/Longitude
- ROT: Rate of Turn
- GSA: GNSS DOP and Active Satellites
"""

import sys
import time
import logging
import argparse
from datetime import datetime, timezone
from typing import Any

import zenoh
import pynmea2
import skarv
import skarv.utilities
import keelson
from skarv.utilities.zenoh import mirror
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    setup_logging,
)

# Subjects to subscribe to
SUBJECTS = [
    "location_fix",
    "speed_over_ground_knots",
    "course_over_ground_deg",
    "heading_true_north_deg",
    "yaw_rate_degps",
    "location_fix_hdop",
    "location_fix_vdop",
    "location_fix_pdop",
    "location_fix_satellites_used",
    "location_fix_undulation_m",
]

ARGS = None
logger = logging.getLogger("keelson2nmea0183")


def unpack(sample: skarv.Sample) -> Any:
    """
    Unpacks a skarv sample containing a zenoh payload into a keelson message.

    """
    subject = sample.key_expr
    _, _, payload = keelson.uncover(sample.value.to_bytes())
    return keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(subject)
    )


def output_nmea(sentence: str):
    """Output an NMEA sentence to STDOUT with proper flushing."""
    sys.stdout.write(sentence + "\n")
    sys.stdout.flush()
    logger.debug(f"Output: {sentence}")


def format_lat_lon(latitude: float, longitude: float) -> tuple:
    """
    Format latitude and longitude for NMEA sentences.

    Returns:
        Tuple of (lat_str, lat_dir, lon_str, lon_dir)
    """
    # Latitude
    lat_deg = abs(int(latitude))
    lat_min = (abs(latitude) - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:07.4f}"
    lat_dir = "N" if latitude >= 0 else "S"

    # Longitude
    lon_deg = abs(int(longitude))
    lon_min = (abs(longitude) - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:07.4f}"
    lon_dir = "E" if longitude >= 0 else "W"

    return lat_str, lat_dir, lon_str, lon_dir


@skarv.trigger("location_fix")
@skarv.trigger("location_fix_hdop")
@skarv.trigger("location_fix_satellites_used")
@skarv.trigger("location_fix_undulation_m")
def generate_gga():
    """Generate GGA sentence when position or related data updates."""
    location_sample = skarv.get("location_fix")
    if not location_sample:
        return

    location = unpack(location_sample)
    if not location:
        return

    # Get additional data from skarv
    hdop_sample = skarv.get("location_fix_hdop")
    sats_sample = skarv.get("location_fix_satellites_used")
    undulation_sample = skarv.get("location_fix_undulation_m")

    hdop = unpack(hdop_sample).value if hdop_sample else None
    num_sats = unpack(sats_sample).value if sats_sample else None
    undulation = unpack(undulation_sample).value if undulation_sample else None

    # Extract timestamp
    utc_time = (
        location.timestamp.ToDatetime()
        if location.timestamp.seconds
        else datetime.now(timezone.utc)
    )

    # Format position
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(
        location.latitude, location.longitude
    )

    # Generate GGA sentence
    try:
        gga = pynmea2.GGA(
            ARGS.talker_id,
            "GGA",
            (
                utc_time.strftime("%H%M%S.%f")[:-4],  # Time (HHMMSS.ss)
                lat_str,
                lat_dir,
                lon_str,
                lon_dir,
                "1",  # Fix quality (1 = GPS fix)
                f"{num_sats:02d}" if num_sats else "00",
                f"{hdop:.1f}" if hdop else "",
                f"{location.altitude:.2f}",
                "M",  # Altitude units
                f"{undulation:.1f}" if undulation else "",
                "M",  # Undulation units
                "",  # Age of differential corrections
                "0000",  # Differential reference station ID
            ),
        )
        output_nmea(str(gga))
    except Exception as e:
        logger.error(f"Failed to generate GGA: {e}")


@skarv.trigger("location_fix")
@skarv.trigger("speed_over_ground_knots")
@skarv.trigger("course_over_ground_deg")
def generate_rmc():
    """Generate RMC sentence when position, speed, or course updates."""
    location_sample = skarv.get("location_fix")
    if not location_sample:
        return

    location = unpack(location_sample)
    if not location:
        return

    # Get speed and course from skarv
    speed_sample = skarv.get("speed_over_ground_knots")
    course_sample = skarv.get("course_over_ground_deg")

    speed = unpack(speed_sample).value if speed_sample else None
    course = unpack(course_sample).value if course_sample else None

    # Generate RMC even with missing speed/course (will use empty fields)
    if speed is None or course is None:
        return  # RMC requires both speed and course

    # Extract timestamp
    utc_time = (
        location.timestamp.ToDatetime()
        if location.timestamp.seconds
        else datetime.now(timezone.utc)
    )

    # Format position
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(
        location.latitude, location.longitude
    )

    try:
        rmc = pynmea2.RMC(
            ARGS.talker_id,
            "RMC",
            (
                utc_time.strftime("%H%M%S.%f")[:-4],  # Time
                "A",  # Status (A = active/valid)
                lat_str,
                lat_dir,
                lon_str,
                lon_dir,
                f"{speed:.1f}",
                f"{course:.1f}",
                utc_time.strftime("%d%m%y"),  # Date (DDMMYY)
                "",  # Magnetic variation
                "",  # Variation direction
                "A",  # Mode indicator (A = autonomous)
            ),
        )
        output_nmea(str(rmc))
    except Exception as e:
        logger.error(f"Failed to generate RMC: {e}")


@skarv.trigger("location_fix")
def generate_gll():
    """Generate GLL sentence when position updates."""
    location_sample = skarv.get("location_fix")
    if not location_sample:
        return

    location = unpack(location_sample)
    if not location:
        return

    # Extract timestamp
    utc_time = (
        location.timestamp.ToDatetime()
        if location.timestamp.seconds
        else datetime.now(timezone.utc)
    )

    # Format position
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(
        location.latitude, location.longitude
    )

    try:
        gll = pynmea2.GLL(
            ARGS.talker_id,
            "GLL",
            (
                lat_str,
                lat_dir,
                lon_str,
                lon_dir,
                utc_time.strftime("%H%M%S.%f")[:-4],
                "A",  # Status (A = valid)
                "A",  # Mode indicator
            ),
        )
        output_nmea(str(gll))
    except Exception as e:
        logger.error(f"Failed to generate GLL: {e}")


@skarv.trigger("speed_over_ground_knots")
@skarv.trigger("course_over_ground_deg")
def generate_vtg():
    """Generate VTG sentence when speed or course updates."""
    speed_sample = skarv.get("speed_over_ground_knots")
    course_sample = skarv.get("course_over_ground_deg")

    speed = unpack(speed_sample).value if speed_sample else None
    course = unpack(course_sample).value if course_sample else None

    if speed is None or course is None:
        return  # VTG requires both speed and course

    try:
        vtg = pynmea2.VTG(
            ARGS.talker_id,
            "VTG",
            (
                f"{course:.1f}",  # True track
                "T",
                "",  # Magnetic track
                "M",
                f"{speed:.1f}",  # Speed in knots
                "N",
                f"{speed * 1.852:.1f}",  # Speed in km/h
                "K",
                "A",  # Mode indicator
            ),
        )
        output_nmea(str(vtg))
    except Exception as e:
        logger.error(f"Failed to generate VTG: {e}")


@skarv.trigger("heading_true_north_deg")
def generate_hdt():
    """Generate HDT sentence when heading updates."""
    heading_sample = skarv.get("heading_true_north_deg")
    if not heading_sample:
        return

    heading = unpack(heading_sample).value
    if heading is None:
        return

    try:
        hdt = pynmea2.HDT(ARGS.talker_id, "HDT", (f"{heading:.1f}", "T"))
        output_nmea(str(hdt))
    except Exception as e:
        logger.error(f"Failed to generate HDT: {e}")


@skarv.trigger("yaw_rate_degps")
def generate_rot():
    """
    Generate ROT sentence when yaw rate updates.

    Note: Keelson uses degrees per second, NMEA ROT uses degrees per minute.
    """
    yaw_rate_sample = skarv.get("yaw_rate_degps")
    if not yaw_rate_sample:
        return

    yaw_rate_degps = unpack(yaw_rate_sample).value
    if yaw_rate_degps is None:
        return

    # Convert from degrees per second to degrees per minute
    yaw_rate_degpm = yaw_rate_degps * 60.0

    try:
        rot = pynmea2.ROT(
            ARGS.talker_id, "ROT", (f"{yaw_rate_degpm:.1f}", "A")  # Status (A = valid)
        )
        output_nmea(str(rot))
    except Exception as e:
        logger.error(f"Failed to generate ROT: {e}")


@skarv.trigger("location_fix_hdop")
@skarv.trigger("location_fix_vdop")
@skarv.trigger("location_fix_pdop")
def generate_gsa():
    """Generate GSA sentence when DOP values update."""
    # Get all DOP values from skarv
    hdop_sample = skarv.get("location_fix_hdop")
    vdop_sample = skarv.get("location_fix_vdop")
    pdop_sample = skarv.get("location_fix_pdop")

    hdop = unpack(hdop_sample).value if hdop_sample else None
    vdop = unpack(vdop_sample).value if vdop_sample else None
    pdop = unpack(pdop_sample).value if pdop_sample else None

    # Need at least one DOP value to generate GSA
    if hdop is None and vdop is None and pdop is None:
        return

    try:
        # GSA sentence with basic data
        # PRN list would require tracking individual satellites (complex)
        gsa = pynmea2.GSA(
            ARGS.talker_id,
            "GSA",
            (
                "A",  # Mode (A = automatic)
                "3",  # Fix type (3 = 3D fix)
                # PRN numbers (empty)
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                f"{pdop:.1f}" if pdop else "",
                f"{hdop:.1f}" if hdop else "",
                f"{vdop:.1f}" if vdop else "",
            ),
        )
        output_nmea(str(gsa))
    except Exception as e:
        logger.error(f"Failed to generate GSA: {e}")


@skarv.utilities.call_every(1.0)
def generate_zda():
    """Generate ZDA sentence periodically (once per second)."""
    try:
        now = datetime.now(timezone.utc)
        zda = pynmea2.ZDA(
            ARGS.talker_id,
            "ZDA",
            (
                now.strftime("%H%M%S.%f")[:-4],  # Time
                f"{now.day:02d}",
                f"{now.month:02d}",
                f"{now.year:04d}",
                "00",  # Local zone hours
                "00",  # Local zone minutes
            ),
        )
        output_nmea(str(zda))
    except Exception as e:
        logger.error(f"Failed to generate ZDA: {e}")


def main():
    global ARGS

    parser = argparse.ArgumentParser(
        prog="keelson2nmea0183",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Subscribe to Keelson/Zenoh and output NMEA0183 to STDOUT",
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

    # NMEA configuration
    parser.add_argument(
        "--talker-id", type=str, default="GP", help="NMEA talker ID (e.g., GP, GN, GL)"
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

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        logger.info(f"Connected to realm: {ARGS.realm}, entity: {ARGS.entity_id}")
        logger.info(f"NMEA talker ID: {ARGS.talker_id}")

        # Mirror all subjects to skarv
        for subject in SUBJECTS:
            source_id = getattr(ARGS, f"source_id_{subject}")
            zenoh_key = keelson.construct_pubsub_key(
                ARGS.realm, ARGS.entity_id, subject, source_id
            )
            mirror(session, zenoh_key, subject)
            logger.info(f"Subscribed to: {zenoh_key}")

        logger.info("Outputting NMEA to STDOUT...")

        # Keep running until interrupted
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
