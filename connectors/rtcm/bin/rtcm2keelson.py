#!/usr/bin/env python3
"""RTCM v3 to Keelson connector.

Reads RTCM v3 correction frames from stdin, parses them using pyrtcm, and
publishes each frame as a TimestampedBytes payload on the keelson bus.

Typical usage with socat:

    socat TCP:base-station:2101 STDOUT | rtcm2keelson -r realm -e gnss -s base/0
"""

import sys
import time
import logging
import argparse

import zenoh
from pyrtcm import RTCMReader, RTCMParseError, RTCMMessageError, RTCMTypeError

import keelson
from keelson.helpers import enclose_from_bytes
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
    GracefulShutdown,
)

logger = logging.getLogger("rtcm2keelson")


def main():
    parser = argparse.ArgumentParser(description="RTCM v3 to Keelson connector")
    add_common_arguments(parser)
    parser.add_argument(
        "-r", "--realm", required=True, type=str, help="Keelson realm (base path)"
    )
    parser.add_argument(
        "-e", "--entity-id", required=True, type=str, help="Entity identifier"
    )
    parser.add_argument(
        "-s", "--source-id", required=True, type=str, help="Source identifier"
    )

    args = parser.parse_args()
    setup_logging(level=args.log_level)

    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)

    key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, "raw_rtcm_v3", args.source_id
    )

    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)

    publisher = session.declare_publisher(key)
    logger.info("Publishing on: %s", key)

    reader = RTCMReader(sys.stdin.buffer)

    with declare_liveliness_token(session, args.realm, args.entity_id, args.source_id):
        with GracefulShutdown() as shutdown:
            try:
                for raw_data, parsed_data in reader:
                    if shutdown.is_requested():
                        break
                    if raw_data is None:
                        continue

                    envelope = enclose_from_bytes(raw_data, time.time_ns())
                    publisher.put(envelope)
                    logger.debug(
                        "Published RTCM frame: %s (%d bytes)",
                        parsed_data.identity if parsed_data else "unknown",
                        len(raw_data),
                    )
            except (RTCMParseError, RTCMMessageError, RTCMTypeError) as exc:
                logger.warning("RTCM parse error: %s", exc)

    publisher.undeclare()
    session.close()
    logger.info("Shut down.")


if __name__ == "__main__":
    main()
