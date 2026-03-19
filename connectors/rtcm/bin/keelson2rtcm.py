#!/usr/bin/env python3
"""Keelson to RTCM v3 connector.

Subscribes to RTCM v3 data on the keelson bus and writes raw RTCM bytes to
stdout.  Pipe through socat for TCP distribution or through ntrip-cli for
NTRIP v1 serving.

Typical usage:

    keelson2rtcm -r realm -e gnss | socat STDIN TCP-LISTEN:2102,reuseaddr
    keelson2rtcm -r realm -e gnss | ntrip-cli --port 2101
"""

import sys
import logging
import argparse

import zenoh

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedBytes
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    setup_logging,
    GracefulShutdown,
)

logger = logging.getLogger("keelson2rtcm")

_shutdown: GracefulShutdown | None = None


def on_rtcm_sample(sample) -> None:
    """Zenoh subscriber callback — extract raw bytes and write to stdout."""
    try:
        _received_at, _enclosed_at, payload_bytes = keelson.uncover(
            sample.payload.to_bytes()
        )
        tb = TimestampedBytes.FromString(payload_bytes)
        sys.stdout.buffer.write(tb.value)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        logger.info("Downstream pipe closed")
        if _shutdown is not None:
            _shutdown.request()
    except Exception:
        logger.exception("Error processing RTCM sample")


def main():
    global _shutdown

    parser = argparse.ArgumentParser(description="Keelson to RTCM v3 connector")
    add_common_arguments(parser)
    parser.add_argument(
        "-r", "--realm", required=True, type=str, help="Keelson realm (base path)"
    )
    parser.add_argument(
        "-e", "--entity-id", required=True, type=str, help="Entity identifier"
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default="**",
        help="Source identifier to subscribe to (default: ** for all)",
    )

    args = parser.parse_args()
    setup_logging(level=args.log_level)

    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)

    key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, "raw_rtcm_v3", args.source_id
    )

    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)

    subscriber = session.declare_subscriber(key, on_rtcm_sample)
    logger.info("Subscribed to: %s", key)

    with GracefulShutdown() as shutdown:
        _shutdown = shutdown
        shutdown.wait()

    subscriber.undeclare()
    session.close()
    logger.info("Shut down.")


if __name__ == "__main__":
    main()
