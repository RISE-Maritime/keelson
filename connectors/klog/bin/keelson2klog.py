#!/usr/bin/env python3

import time
import atexit
import logging
import pathlib
import warnings
import argparse
from io import BufferedWriter
from queue import Queue, Empty
from threading import Thread, Event
from contextlib import contextmanager

import zenoh

from keelson.Envelope_pb2 import KeyEnvelopePair
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
)

logger = logging.getLogger("klog-record")


@contextmanager
def ignore(*exceptions):
    try:
        yield
    except exceptions:
        logger.exception("Something went wrong in the listener!")


def write_message(writer: BufferedWriter, received_at: int, key: str, envelope: bytes):
    logger.debug("Writing to file: key=%s, log_time=%s", key, received_at)

    data = KeyEnvelopePair()
    data.timestamp.FromNanoseconds(received_at)
    data.key = key
    data.envelope = envelope

    serialized_data = data.SerializeToString()

    serialized_length = len(serialized_data).to_bytes(4, "big", signed=False)

    writer.write(serialized_length + serialized_data)


def run(session: zenoh.Session, args: argparse.Namespace):
    queue = Queue()

    close_down = Event()

    def _recorder():
        with args.output.open("wb") as fh:
            while not close_down.is_set():
                try:
                    received_at, sample = queue.get(timeout=0.01)
                except Empty:
                    continue

                with ignore(Exception):
                    key = str(sample.key_expr)
                    logger.debug("Received sample on key: %s", key)

                    write_message(fh, received_at, key, bytes(sample.payload))

    t = Thread(target=_recorder)
    t.daemon = True
    t.start()

    # And start subscribing
    subscribers = [
        session.declare_subscriber(key, lambda s: queue.put((time.time_ns(), s)))
        for key in args.key
    ]

    while True:
        try:
            qsize = queue.qsize()
            logger.debug("Approximate queue size is: %s", qsize)

            if qsize > 100:
                warnings.warn(f"Queue size is {qsize}")
            elif qsize > 1000:
                raise RuntimeError(
                    f"Recorder is not capable of keeping up with data flow. Current queue size is {qsize}. Exiting!"
                )

            time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Closing down on user request!")
            logger.debug("Undeclaring subscribers...")
            for sub in subscribers:
                sub.undeclare()

            logger.debug("Waiting for all items in queue to be processed...")
            while not queue.empty():
                time.sleep(0.1)

            logger.debug("Joining recorder thread...")
            close_down.set()
            t.join()

            logger.debug("Done! Good bye :)")
            break


def main():
    parser = argparse.ArgumentParser(
        prog="klog-record",
        description="A pure python klog recorder for keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)

    parser.add_argument(
        "-k",
        "--key",
        type=str,
        action="append",
        required=True,
        help="Key expressions to subscribe to from the Zenoh session",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        required=True,
        help="File path to write recording to",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

    # Put together zenoh session configuration
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    # Construct session
    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)

    def _on_exit():
        session.close()

    atexit.register(_on_exit)

    run(session, args)


if __name__ == "__main__":
    main()
