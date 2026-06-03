#!/usr/bin/env python3

"""
Command line utility tool for processing AIS encoded input from stdin and
outputting on a Zenoh session adhearing to the keelson protocol.
"""

import sys
import math
import time
import json
import logging
import argparse
import threading
from datetime import datetime, timezone
from typing import Union
from contextlib import contextmanager

import zenoh
from geopy.distance import distance
from pyais.queue import NMEAQueue
from pyais.filter import GridFilter
from pyais.messages import (
    MessageType1,
    MessageType2,
    MessageType3,
    MessageType5,
    MessageType18,
)

import keelson
from keelson.helpers import (
    enclose_from_bytes,
    enclose_from_float,
    enclose_from_integer,
    enclose_from_lon_lat,
    enclose_from_string,
    enclose_from_timestamp,
)
from keelson.payloads.VesselNavStatus_pb2 import VesselNavStatus
from keelson.payloads.VesselType_pb2 import VesselType as VesselTypePb
from keelson.scaffolding import declare_liveliness_token, make_configurable

logger = logging.getLogger("ais2keelson")

# AIS "not available" sentinel values (per ITU-R M.1371-5)
AIS_HEADING_NOT_AVAILABLE = 511
AIS_COG_NOT_AVAILABLE = 360.0
AIS_SOG_NOT_AVAILABLE = 102.3
AIS_ROT_NOT_AVAILABLE = 128  # ±128 both mean not available

PUBLISHERS: dict[str, zenoh.Publisher] = {}
QUEUE = NMEAQueue()

# Filters with initial settings
GRID_FILTER = GridFilter(
    lat_max=math.inf, lat_min=-math.inf, lon_max=math.inf, lon_min=-math.inf
)

MSG5_DB: dict[int, MessageType5] = {}


@contextmanager
def ignore(*exception):
    try:
        yield
    except exception as e:
        logger.exception("Something went wrong in the dispatcher!", exc_info=e)


def _enclose_nav_status(ais_status_value: int, timestamp: int = None) -> bytes:
    payload = VesselNavStatus()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    # Keelson enum is AIS standard + 1 (to reserve 0 for UNKNOWN in protobuf)
    payload.navigation_status = ais_status_value + 1
    return keelson.enclose(payload.SerializeToString())


def _enclose_vessel_type(ais_ship_type_value: int, timestamp: int = None) -> bytes:
    payload = VesselTypePb()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.vessel_type = ais_ship_type_value
    return keelson.enclose(payload.SerializeToString())


def _ais_eta_to_nanoseconds(month: int, day: int, hour: int, minute: int) -> int | None:
    """Convert AIS ETA fields to a UTC timestamp in nanoseconds.

    AIS does not include the year, so we infer it from the current date.
    Returns None if the ETA is marked as not available.
    """
    # AIS "not available" sentinel values
    if month == 0 or day == 0 or hour == 24 or minute == 60:
        return None

    now = datetime.now(timezone.utc)
    year = now.year

    try:
        eta = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None

    # If ETA is more than 6 months in the past, assume next year
    if (now - eta).days > 180:
        try:
            eta = datetime(year + 1, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None

    return int(eta.timestamp() * 1_000_000_000)


def get_config() -> dict:
    return {
        "lat_max": GRID_FILTER.lat_max,
        "lat_min": GRID_FILTER.lat_min,
        "lon_max": GRID_FILTER.lon_max,
        "lon_min": GRID_FILTER.lon_min,
    }


def set_config(config: dict):
    for key, value in config.items():
        setattr(GRID_FILTER, key, value)


def _translate_position_to_geometrical_center(
    msg123: Union[MessageType1, MessageType2, MessageType3],
):
    # Validate coordinates are within valid ranges (AIS uses 91/181 for "not available")
    if not (-90 <= msg123.lat <= 90 and -180 <= msg123.lon <= 180):
        return

    # Validate heading is available (AIS uses 511 for "not available")
    if not (0 <= msg123.heading < 360):
        return

    if not (msg5 := MSG5_DB.get(msg123.mmsi)):
        return

    move_to_bow = (msg5.to_bow - msg5.to_stern) / 2
    move_to_starboard = (msg5.to_starboard - msg5.to_port) / 2

    p1 = distance(meters=move_to_bow).destination(
        (msg123.lat, msg123.lon), msg123.heading
    )
    p2 = distance(meters=move_to_starboard).destination(p1, msg123.heading + 90)

    msg123.lat = p2.latitude
    msg123.lon = p2.longitude


def _handle_AIS_message_123(
    msg: Union[MessageType1, MessageType2, MessageType3], timestamp: int = None
):
    yield "location_fix", enclose_from_lon_lat(msg.lon, msg.lat, timestamp=timestamp)
    if abs(msg.turn) != AIS_ROT_NOT_AVAILABLE:
        yield "yaw_rate_degps", enclose_from_float(msg.turn / 60.0, timestamp=timestamp)
    if msg.heading != AIS_HEADING_NOT_AVAILABLE:
        yield "heading_true_north_deg", enclose_from_float(
            msg.heading, timestamp=timestamp
        )
    if msg.course != AIS_COG_NOT_AVAILABLE:
        yield "course_over_ground_deg", enclose_from_float(
            msg.course, timestamp=timestamp
        )
    if msg.speed != AIS_SOG_NOT_AVAILABLE:
        yield "speed_over_ground_knots", enclose_from_float(
            msg.speed, timestamp=timestamp
        )
    yield "mmsi_number", enclose_from_integer(msg.mmsi, timestamp=timestamp)
    yield "nav_status", _enclose_nav_status(msg.status, timestamp=timestamp)


def _handle_AIS_message_5(msg: MessageType5, timestamp: int = None):
    yield "draught_mean_m", enclose_from_float(msg.draught, timestamp=timestamp)
    yield "length_over_all_m", enclose_from_float(
        msg.to_bow + msg.to_stern, timestamp=timestamp
    )
    yield "breadth_over_all_m", enclose_from_float(
        msg.to_port + msg.to_starboard, timestamp=timestamp
    )
    yield "name", enclose_from_string(msg.shipname, timestamp=timestamp)
    yield "call_sign", enclose_from_string(msg.callsign, timestamp=timestamp)
    yield "imo_number", enclose_from_integer(msg.imo, timestamp=timestamp)
    yield "vessel_type", _enclose_vessel_type(msg.ship_type, timestamp=timestamp)
    yield "destination", enclose_from_string(msg.destination, timestamp=timestamp)
    eta_ns = _ais_eta_to_nanoseconds(msg.month, msg.day, msg.hour, msg.minute)
    if eta_ns is not None:
        yield "eta", enclose_from_timestamp(eta_ns, timestamp=timestamp)


def _handle_AIS_message_18(msg: MessageType18, timestamp: int = None):
    yield "location_fix", enclose_from_lon_lat(msg.lon, msg.lat, timestamp=timestamp)
    if msg.heading != AIS_HEADING_NOT_AVAILABLE:
        yield "heading_true_north_deg", enclose_from_float(
            msg.heading, timestamp=timestamp
        )
    if msg.course != AIS_COG_NOT_AVAILABLE:
        yield "course_over_ground_deg", enclose_from_float(
            msg.course, timestamp=timestamp
        )
    if msg.speed != AIS_SOG_NOT_AVAILABLE:
        yield "speed_over_ground_knots", enclose_from_float(
            msg.speed, timestamp=timestamp
        )
    yield "mmsi_number", enclose_from_integer(msg.mmsi, timestamp=timestamp)


HANDLERS = {
    1: _handle_AIS_message_123,
    2: _handle_AIS_message_123,
    3: _handle_AIS_message_123,
    5: _handle_AIS_message_5,
    18: _handle_AIS_message_18,
}


def _get_or_declare_publisher(session: zenoh.Session, key: str) -> zenoh.Publisher:
    pub = PUBLISHERS.get(key)
    if pub is None:
        logger.debug("Declaring publisher for key: %s", key)
        pub = session.declare_publisher(key)
        PUBLISHERS[key] = pub
    return pub


def run(session: zenoh.Session, args: argparse.Namespace):
    def _dispatcher():
        logger.debug("Dispatcher thread started!")

        def _message_generator():
            while True:
                try:
                    logger.debug("Waiting for new message from Queue...")
                    yield QUEUE.get().decode()
                except Exception:
                    logger.exception("Failed to decode AIS Sentence!")

        for msg in GRID_FILTER.filter(_message_generator()):
            with ignore(Exception):
                logger.debug("Got new msg: %s", msg)
                timestamp = time.time_ns()
                mmsi = msg.mmsi

                if msg.msg_type == 5:
                    MSG5_DB[mmsi] = msg
                elif msg.msg_type in (1, 2, 3):
                    _translate_position_to_geometrical_center(msg)

                if args.publish_json:
                    key = keelson.construct_pubsub_key(
                        args.realm,
                        args.entity_id,
                        "raw_json",
                        f"{args.source_id}/{mmsi}",
                    )
                    pub = _get_or_declare_publisher(session, key)
                    pub.put(
                        enclose_from_bytes(msg.to_json().encode(), timestamp=timestamp)
                    )

                if args.publish_fields and (handler := HANDLERS.get(msg.msg_type)):
                    logger.debug("Publishing fields!")
                    target_id = f"mmsi_{mmsi}"
                    for subject, envelope in handler(msg, timestamp=timestamp):
                        key = keelson.construct_pubsub_key(
                            args.realm,
                            args.entity_id,
                            subject,
                            f"{args.source_id}",
                            target_id=target_id,
                        )
                        logger.debug(
                            "Publishing subject: %s for target_id: %s key=%s",
                            subject,
                            target_id,
                            key,
                        )
                        pub = _get_or_declare_publisher(session, key)
                        pub.put(envelope)

    t = threading.Thread(target=_dispatcher, daemon=True)
    t.start()

    try:
        for line in sys.stdin.buffer:
            logger.debug("Read from stdin: %s", line)

            if args.publish_raw:
                logger.debug("Publishing raw")
                key = keelson.construct_pubsub_key(
                    args.realm, args.entity_id, "raw", args.source_id
                )
                pub = _get_or_declare_publisher(session, key)
                pub.put(enclose_from_bytes(line))

            if args.publish_json or args.publish_fields:
                logger.debug("Adding to NMEA queue")
                QUEUE.put_line(line)

    except KeyboardInterrupt:
        logger.info("Closing down...")
        logger.debug("Waiting for all items in queue to be processed...")
        while not QUEUE.empty():
            time.sleep(0.1)
        logger.debug("Good bye!")


def main():
    parser = argparse.ArgumentParser(
        prog="ais2keelson",
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
    parser.add_argument("--publish-json", default=False, action="store_true")
    parser.add_argument("--publish-fields", default=False, action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)

    conf = zenoh.Config()

    if args.mode is not None:
        conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

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
                get_config,
                set_config,
            )
            run(session, args)


if __name__ == "__main__":
    main()
