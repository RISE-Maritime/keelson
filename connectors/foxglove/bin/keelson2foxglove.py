#!/usr/bin/env python3

import sys
import time
import pathlib
import logging
import argparse
import warnings
from typing import Dict
from queue import Queue, Empty
from threading import Thread, Event
from contextlib import contextmanager

import zenoh
import keelson
import foxglove
from foxglove import Channel, Schema
from foxglove.websocket import (
    Capability,
    ChannelView,
    Client,
    ServerListener,
)
from google.protobuf.message import DecodeError
from keelson_connectors_common import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
)

logger = logging.getLogger("foxglove-liveview")

MAIN_LOOP_SLEEP_TIME = 10.0  # seconds


@contextmanager
def ignore(*exception):
    try:
        yield
    except exception as e:
        logger.exception(
            "Something unexpected went wrong in the ws publisher!", exc_info=e
        )


class KeelsonListener(ServerListener):
    def __init__(self) -> None:
        # Map client id -> set of subscribed topics
        self.subscribers: dict[int, set[str]] = {}

    def has_subscribers(self) -> bool:
        return len(self.subscribers) > 0

    def on_subscribe(
        self,
        client: Client,
        channel: ChannelView,
    ) -> None:
        """
        Called by the server when a client subscribes to a channel.
        We'll use this and on_unsubscribe to simply track if we have any subscribers at all.
        """
        logging.info(f"Client {client} subscribed to channel {channel.topic}")
        self.subscribers.setdefault(client.id, set()).add(channel.topic)

    def on_unsubscribe(
        self,
        client: Client,
        channel: ChannelView,
    ) -> None:
        """
        Called by the server when a client unsubscribes from a channel.
        """
        logging.info(f"Client {client} unsubscribed from channel {channel.topic}")
        self.subscribers[client.id].remove(channel.topic)
        if not self.subscribers[client.id]:
            del self.subscribers[client.id]


def run(session: zenoh.Session, args: argparse.Namespace):

    logger.info("Starting ws server on %s:%s", args.ws_host, args.ws_port)

    listener = KeelsonListener()

    server = foxglove.start_server(
        host=args.ws_host,
        port=args.ws_port,
        server_listener=listener,
        capabilities=[Capability.ClientPublish],
        supported_encodings=["protobuf"],
    )

    queue = Queue()
    close_down = Event()

    def _ws_publisher():
        channels: Dict[str, Channel] = {}

        while not close_down.is_set():
            with ignore(Exception):
                try:
                    sample: zenoh.Sample = queue.get(timeout=0.01)
                except Empty:
                    continue

                key = str(sample.key_expr)
                logger.debug("Received sample on key: %s", key)

                # if not listener.has_subscribers():
                #     logger.debug("No listeners, doing nothing!")
                #     continue

                # Uncover from keelson envelope
                try:
                    received_at, enclosed_at, payload = keelson.uncover(
                        sample.payload.to_bytes()
                    )
                except DecodeError:
                    logger.exception(
                        "Key %s did not contain a valid keelson.Envelope: %s",
                        key,
                        sample.payload.to_bytes(),
                    )
                    continue

                # If this key is known, write message to file
                if key in channels:
                    logger.debug("Key %s is already known!", key)
                    channels[key].log(
                        payload, log_time=received_at, publish_time=enclosed_at
                    )
                    continue

                # Else, lets start finding out about schemas etc
                try:
                    subject = keelson.get_subject_from_pubsub_key(key)
                except ValueError:
                    logger.exception(
                        "Received key did not match the expected format: %s",
                        key,
                    )
                    continue

                logger.info("Unseen key: %s", key)

                if not keelson.is_subject_well_known(subject):
                    logger.info("Unknown subject, skipping...")
                    continue

                logger.info("Subject %s is well-known!", subject)
                # Get info about the well-known subject
                keelson_schema = keelson.get_subject_schema(subject)

                file_descriptor_set = (
                    keelson.get_protobuf_file_descriptor_set_from_type_name(
                        keelson_schema
                    )
                )

                logger.debug(
                    "Registering a channel (%s) with schema_name=%s",
                    key,
                    keelson_schema,
                )

                channel = channels[key] = Channel(
                    topic=key,
                    message_encoding="protobuf",
                    schema=Schema(
                        name=keelson_schema,
                        encoding="protobuf",
                        data=file_descriptor_set.SerializeToString(),
                    ),
                )

                # Finally, write the message to the socket
                logger.debug("...and writing the actual message to file!")
                channel.log(payload, log_time=received_at, publish_time=enclosed_at)

    ws_publisher_thread = Thread(target=_ws_publisher)
    ws_publisher_thread.daemon = True
    ws_publisher_thread.start()

    # And start subscribing
    logger.info("Starting subscribers")
    subscribers = [session.declare_subscriber(key, queue.put) for key in args.key]

    while True:
        try:
            # Check queue size
            qsize = queue.qsize()
            logger.info(f"Approximate queue size is: {qsize}")

            if qsize > 100:
                warnings.warn(f"Queue size is {qsize}")
            elif qsize > 1000:
                raise RuntimeError(
                    f"Websocket publisher is not capable of keeping up with data flow. Current queue size is {qsize}. Exiting!"
                )

            time.sleep(MAIN_LOOP_SLEEP_TIME)
        except KeyboardInterrupt:
            logger.info("Closing down on user request!")
            logger.debug("Undeclaring subscribers...")
            for sub in subscribers:
                sub.undeclare()

            logger.debug("Waiting for all items in queue to be processed...")
            while not queue.empty():
                time.sleep(0.1)

            logger.debug("Joining websocket publisher thread...")
            close_down.set()
            ws_publisher_thread.join()

            logger.debug("Stopping websocket server...")
            server.stop()

            logger.debug("Done! Good bye :)")
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="foxglove-liveview",
        description="A foxglove websocket server for keelson",
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

    parser.add_argument("--ws-host", type=str, default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=8765)

    def _parse_pair(arg) -> tuple[pathlib.Path, pathlib.Path]:
        path_to_subject_yaml, path_to_proto_types = arg.split(",")
        return pathlib.Path(path_to_subject_yaml), (
            pathlib.Path(path_to_proto_types) if path_to_proto_types else None
        )

    parser.add_argument(
        "--extra-subjects-types",
        type=_parse_pair,
        action="append",
        help="Add additional well-known subjects and protobuf types as --extra-subjects-types=path/to/subjects.yaml,path_to_protobuf_file_descriptor_set.bin",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)
    foxglove.set_log_level(args.log_level)

    # Loading extra well-known subjects and types if provided
    if extra_paths := args.extra_subjects_types:
        for pair in extra_paths:
            logger.info("Loading extra subjects (%s) and types (%s)", *pair)
            keelson.add_well_known_subjects_and_proto_definitions(*pair)

    # Put together zenoh session configuration
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    # Construct session
    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        try:
            run(session, args)
        except KeyboardInterrupt:
            logger.info("Closing down on user request!")
            sys.exit(0)
