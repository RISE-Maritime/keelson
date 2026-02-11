#!/usr/bin/env python3

"""
Command line utility tool for processing input from stdin. Each line on the
input stream is base64 encoded with no wrapping and ended with a newline.
"""

import time
import json
import logging
import argparse
from typing import Dict
from contextlib import contextmanager

import certifi
import zenoh
import paho.mqtt.client as mqtt
from geopy.distance import distance

import keelson
from keelson.helpers import (
    enclose_from_float,
    enclose_from_integer,
    enclose_from_lon_lat,
    enclose_from_string,
)
from keelson.scaffolding import declare_liveliness_token, make_configurable

logger = logging.getLogger("digitraffic2keelson")

# Helper types for readability
LocationMessage = Dict
MetadataMessage = Dict

METADATA_DB: dict[int, MetadataMessage] = {}


@contextmanager
def ignore(*exception):
    try:
        yield
    except exception as e:
        logger.exception("Something went wrong in the dispatcher!", exc_info=e)


# Helper function for translating the antenna position


def _translate_position_to_geometrical_center(
    mmsi: int,
    position_msg: dict,
):
    if not (metadata_msg := METADATA_DB.get(mmsi)):
        # We have no msg5 yet, not much we can do here...
        return

    # How much should we move it?
    move_to_bow = (metadata_msg["refA"] - metadata_msg["refB"]) / 2
    move_to_starboard = (metadata_msg["refD"] - metadata_msg["refC"]) / 2

    # Make the move
    p1 = distance(meters=move_to_bow).destination(
        (position_msg["lat"], position_msg["lon"]), position_msg["heading"]
    )
    p2 = distance(meters=move_to_starboard).destination(
        p1, position_msg["heading"] + 90
    )

    # Update msg123 with corrected values
    position_msg["lat"] = p2.latitude
    position_msg["lon"] = p2.longitude


# AIS Message Handlers


def _handle_location_message(mmsi: int, msg: LocationMessage, timestamp: int = None):
    yield "location_fix", enclose_from_lon_lat(
        msg["lon"], msg["lat"], timestamp=timestamp
    )
    # AIS provides rate of turn in degrees per minute, convert to degrees per second for keelson
    yield "yaw_rate_degps", enclose_from_float(msg["rot"] / 60.0, timestamp=timestamp)
    yield "heading_true_north_deg", enclose_from_float(
        msg["heading"], timestamp=timestamp
    )
    yield "course_over_ground_deg", enclose_from_float(msg["cog"], timestamp=timestamp)
    yield "speed_over_ground_knots", enclose_from_float(msg["sog"], timestamp=timestamp)
    yield "mmsi_number", enclose_from_integer(mmsi, timestamp=timestamp)


def _handle_metadata_message(mmsi: int, msg: MetadataMessage, timestamp: int = None):
    yield "draught_mean_m", enclose_from_float(
        msg["draught"] / 10.0, timestamp=timestamp
    )
    yield "length_over_all_m", enclose_from_float(
        msg["refA"] + msg["refB"], timestamp=timestamp
    )
    yield "breadth_over_all_m", enclose_from_float(
        msg["refC"] + msg["refD"], timestamp=timestamp
    )
    yield "name", enclose_from_string(msg["name"], timestamp=timestamp)
    yield "call_sign", enclose_from_string(msg["callSign"], timestamp=timestamp)
    yield "imo_number", enclose_from_integer(msg["imo"], timestamp=timestamp)


HANDLERS = {
    "location": _handle_location_message,
    "metadata": _handle_metadata_message,
}

# Main loop


def run(session: zenoh.Session, args: argparse.Namespace):
    mq = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        transport="websockets",
    )

    @mq.connect_callback()
    def _(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Connected to digitraffic websocket api!")
            client.subscribe("vessels-v2/#")
        else:
            logger.error(
                "Failed to connect to digitraffic, return code %d\n", reason_code
            )

    @mq.disconnect_callback()
    def _(
        client, userdata, flags, reason_code, props=None
    ):  # pylint: disable=unused-argument
        if reason_code != 0:
            logger.error(
                "Disconnected from %s with reason code: %s", client, reason_code
            )

    @mq.message_callback()
    def _(client, userdata, msg):
        with ignore(Exception):
            logger.debug("Got new msg: %s", msg)
            timestamp = time.time_ns()

            # Parse topic: expected format is "vessels-v2/<mmsi>/<msg_type>"
            # Skip status messages like "vessels-v2/status" (only 2 parts)
            parts = msg.topic.split("/")
            if len(parts) != 3:
                logger.debug("Skipping non-vessel topic: %s", msg.topic)
                return

            _, mmsi_str, msg_type = parts

            try:
                mmsi = int(mmsi_str)
            except ValueError:
                logger.warning("Invalid MMSI in topic: %s", msg.topic)
                return

            try:
                payload = json.loads(msg.payload.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.exception(f"Could not decode payload: {msg.payload}")
                return

            target_id = f"mmsi_{mmsi}"

            if args.publish_raw:
                key = keelson.construct_pubsub_key(
                    args.realm,
                    args.entity_id,
                    "raw_json",
                    f"{args.source_id}",
                    target_id=target_id,
                )

                session.put(
                    key, enclose_from_string(json.dumps(payload), timestamp=timestamp)
                )

            # Handle correction of antenna position
            if msg_type == "metadata":
                METADATA_DB[mmsi] = payload
            elif msg_type == "location":
                _translate_position_to_geometrical_center(mmsi, payload)
            else:
                logger.warning("Unknown msg_type=%s in topic: %s", msg_type, msg.topic)
                return

            if args.publish_fields and (handler := HANDLERS.get(msg_type)):
                logger.debug("Publishing fields!")

                for subject, envelope in handler(mmsi, payload, timestamp=timestamp):
                    key = keelson.construct_pubsub_key(
                        args.realm,
                        args.entity_id,
                        subject,
                        f"{args.source_id}",
                        target_id=target_id,
                    )
                    logger.debug(
                        "Publishing subject: %s for target_id: %s",
                        subject,
                        target_id,
                    )
                    session.put(key, envelope)

    # Do the actual connection
    mq.tls_set(ca_certs=certifi.where())
    mq.connect("meri.digitraffic.fi", 443)

    try:
        mq.loop_forever()
    except KeyboardInterrupt:
        logger.info("Closing down...")
        mq.disconnect()
        logger.debug("Good bye!")


# Entrypoint


def main():
    parser = argparse.ArgumentParser(
        prog="digitraffic2keelson",
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
    parser.add_argument("-s", "--source-id", type=str, required=True)

    parser.add_argument("--publish-raw", default=False, action="store_true")
    parser.add_argument("--publish-fields", default=False, action="store_true")

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)

    # Put together zenoh session configuration
    conf = zenoh.Config()

    if args.mode is not None:
        conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

    # Construct session and run
    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            make_configurable(
                session,
                args.realm,
                args.entity_id,
                args.source_id,
                lambda: dict(),
                lambda x: None,
            )

            # Time to run!
            run(session, args)


if __name__ == "__main__":
    main()
