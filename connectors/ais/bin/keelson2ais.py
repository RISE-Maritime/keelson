#!/usr/bin/env python3

"""
Command line utility tool for outputting AIS encoded messages to stdout
from data received on a Zenoh session adhearing to the keelson protocol.
"""
import sys
import time
import json
import logging
import argparse


import zenoh
import skarv
import skarv.utilities
import skarv.middlewares
from skarv.utilities.zenoh import mirror
import keelson

from pyais import encode_dict

logger = logging.getLogger("keelson2ais")


def _unpack(sample):
    """Unpack a skarv sample into a keelson message."""
    _, _, payload = keelson.uncover(sample.payload.to_bytes())
    return keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(sample.key_expr)
    )


SUBJECTS = [
    # Message 1
    "location_fix",
    "yaw_rate_degps",
    "heading_true_north_deg",
    "course_over_ground_deg",
    "speed_over_ground_knots",
    "mmsi_number",
    # Message 5
    "draught_mean_m",
    "length_over_all_m",
    "breadth_over_all_m",
    "name",
    "call_sign",
    "imo_number",
]

ARGS: argparse.Namespace = None


@skarv.trigger("location_fix")
def _():
    # Fetch location fix from skarv's vault
    if not (location_sample := skarv.get("location_fix")):
        return
    location_fix = _unpack(location_sample)

    # Fetch all other values from skarvs vault
    if not (mmsi_sample := skarv.get("mmsi_number")):
        logger.warning("No MMSI number found, skipping AIS Message 1 creation.")
        return

    yaw_rate_degps = skarv.get("yaw_rate_degps")
    heading_true_north_deg = skarv.get("heading_true_north_deg")
    course_over_ground_deg = skarv.get("course_over_ground_deg")
    speed_over_ground_knots = skarv.get("speed_over_ground_knots")

    # Create AIS Message 1
    # Note: AIS uses degrees per minute, but keelson uses degrees per second
    messages = encode_dict(
        {
            "type": 1,
            "mmsi": _unpack(mmsi_sample).value,
            "lat": location_fix.latitude,
            "lon": location_fix.longitude,
            "course": (
                _unpack(course_over_ground_deg).value
                if course_over_ground_deg
                else None
            ),
            "heading": (
                _unpack(heading_true_north_deg).value
                if heading_true_north_deg
                else None
            ),
            "speed": (
                _unpack(speed_over_ground_knots).value
                if speed_over_ground_knots
                else None
            ),
            "turn": (
                _unpack(yaw_rate_degps).value * 60 if yaw_rate_degps else None
            ),  # Convert deg/s to deg/min
        },
        talker_id=ARGS.talker_id,
        radio_channel=ARGS.radio_channel,
    )

    # Output AIS message(s) to stdout
    for message in messages:
        sys.stdout.write(message + "\n")
    sys.stdout.flush()


def send_message_5():
    # Fetch the MMSI number from skarv
    # If it is not available, we cannot create AIS Message 5
    # so we log a warning and return early.
    if not (mmsi_sample := skarv.get("mmsi_number")):
        logger.warning("No MMSI number found, skipping AIS Message 5 creation.")
        return

    # Fetch all other values from skarvs vault
    draught_mean_m = skarv.get("draught_mean_m")
    length_over_all_m = skarv.get("length_over_all_m")
    breadth_over_all_m = skarv.get("breadth_over_all_m")
    name = skarv.get("name")
    call_sign = skarv.get("call_sign")
    imo_number = skarv.get("imo_number")

    # Create AIS Message 5
    messages = encode_dict(
        {
            "type": 5,
            "mmsi": _unpack(mmsi_sample).value,
            "draught": _unpack(draught_mean_m).value if draught_mean_m else None,
            "to_bow": (
                _unpack(length_over_all_m).value / 2 if length_over_all_m else None
            ),
            "to_stern": (
                _unpack(length_over_all_m).value / 2 if length_over_all_m else None
            ),
            "to_port": (
                _unpack(breadth_over_all_m).value / 2 if breadth_over_all_m else None
            ),
            "to_starboard": (
                _unpack(breadth_over_all_m).value / 2 if breadth_over_all_m else None
            ),
            "shipname": _unpack(name).value if name else None,
            "callsign": _unpack(call_sign).value if call_sign else None,
            "imo": _unpack(imo_number).value if imo_number else None,
        },
        talker_id=ARGS.talker_id,
        radio_channel=ARGS.radio_channel,
    )

    # Output AIS Message 5 to stdout
    for message in messages:
        sys.stdout.write(message + "\n")
    sys.stdout.flush()


# Entrypoint
def main():
    parser = argparse.ArgumentParser(
        prog="keelson2ais",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--log-level", type=int, default=logging.INFO)

    parser.add_argument(
        "--mode",
        "-m",
        dest="mode",
        choices=["peer", "client"],
        type=str,
        help="The zenoh session mode.",
    )

    parser.add_argument(
        "--connect",
        action="append",
        type=str,
        help="Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447",
    )

    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)

    parser.add_argument("--talker-id", type=str, default="AIVDO")
    parser.add_argument("--radio-channel", type=str, default="A")

    parser.add_argument(
        "--msg1-at-most-every",
        type=float,
        default=0.0,
        help="Throttle AIS Message 1 to be sent at most once every N seconds (e.g., 1.0 for at most once per second). Default 0.0 means no throttling.",
    )

    parser.add_argument(
        "--msg5-period",
        type=float,
        default=300,
        help="Periodic interval for AIS Message 5 in seconds.",
    )

    for subject in SUBJECTS:
        parser.add_argument(f"--source_id_{subject}", type=str, default="**")

    # Parse arguments and start doing our thing
    ARGS: argparse.Namespace = parser.parse_args()

    # Setup logger
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=ARGS.log_level
    )
    logging.captureWarnings(True)

    # Put together zenoh session configuration
    conf = zenoh.Config()

    if ARGS.mode is not None:
        conf.insert_json5("mode", json.dumps(ARGS.mode))
    if ARGS.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(ARGS.connect))

    # Register throttle middleware for message 1
    logger.info(
        f"Registering throttle middleware for message 1 (at most every {ARGS.msg1_at_most_every}s)"
    )
    skarv.register_middleware(
        "location_fix", skarv.middlewares.throttle(ARGS.msg1_at_most_every)
    )

    # Set up periodic message 5 sending
    logger.info(f"Message 5 will be sent every {ARGS.msg5_period} seconds")
    skarv.utilities.call_every(ARGS.msg5_period, wait_first=True)(send_message_5)

    # Construct session and run
    logger.info("Opening Zenoh session...")
    zenoh.init_log_from_env_or(logging.getLevelName(ARGS.log_level))
    with zenoh.open(conf) as session:
        # Mirror the subjects from Zenoh to skarv
        for subject in SUBJECTS:
            mirror(
                session,
                keelson.construct_pubsub_key(
                    ARGS.realm,
                    ARGS.entity_id,
                    subject,
                    getattr(ARGS, f"source_id_{subject}"),
                ),
                subject,
            )

        while True:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received, shutting down...")
                break


if __name__ == "__main__":
    main()
