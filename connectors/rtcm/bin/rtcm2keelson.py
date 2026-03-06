#!/usr/bin/env python3
"""RTCM v3 to Keelson connector.

Connects to a TCP base station streaming RTCM v3 corrections, parses frames
using pyrtcm, and publishes each frame as a TimestampedBytes payload on the
keelson bus.
"""

import time
import socket
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

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0


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
    parser.add_argument(
        "--host", required=True, type=str, help="TCP host of RTCM base station"
    )
    parser.add_argument(
        "--port", required=True, type=int, help="TCP port of RTCM base station"
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

    with declare_liveliness_token(session, args.realm, args.entity_id, args.source_id):
        with GracefulShutdown() as shutdown:
            backoff = INITIAL_BACKOFF

            while not shutdown.is_requested():
                sock = None
                stream = None
                try:
                    logger.info("Connecting to %s:%d ...", args.host, args.port)
                    sock = socket.create_connection((args.host, args.port), timeout=10)
                    stream = sock.makefile("rb")
                    reader = RTCMReader(stream)

                    logger.info("Connected, reading RTCM frames...")
                    backoff = INITIAL_BACKOFF

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

                except (OSError, ConnectionError) as exc:
                    logger.warning(
                        "Connection error: %s. Reconnecting in %.1fs...",
                        exc,
                        backoff,
                    )
                except (RTCMParseError, RTCMMessageError, RTCMTypeError) as exc:
                    logger.warning("RTCM parse error (skipping frame): %s", exc)
                    continue
                finally:
                    if stream:
                        stream.close()
                    if sock:
                        sock.close()

                if not shutdown.is_requested():
                    shutdown.wait(timeout=backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)

    publisher.undeclare()
    session.close()
    logger.info("Shut down.")


if __name__ == "__main__":
    main()
