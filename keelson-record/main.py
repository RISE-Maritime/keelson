import sys
import json
import time
import atexit
import logging
import pathlib
import warnings
import argparse
from queue import Queue
from threading import Thread

import zenoh
from zenoh import Reliability, Sample
from mcap.writer import Writer, CompressionType
from mcap.well_known import SchemaEncoding, MessageEncoding


import brefv

logger = logging.getLogger("keelson-record")

BREFV_TO_MCAP_SCHEMA_ENCODING_MAP = {"json": "jsonschema", "protobuf": "protobuf"}

BREFV_TO_MCAP_MESSAGE_ENCODING_MAP = {"json": "json", "protobuf": "protobuf"}


def run(session: zenoh.Session, args: argparse.Namespace):
    queue = Queue()

    with args.output.open("wb") as fh:
        # Initiate writer
        writer = Writer(fh)
        writer.start()

        schemas = {}
        channels = {}

        def _recorder():
            while item := queue.get():
                channel_id, received_at, enclosed_at, payload = item
                writer.add_message(
                    channel_id=channel_id,
                    log_time=received_at,
                    publish_time=enclosed_at,
                    data=payload,
                )

        t = Thread(target=_recorder)
        t.daemon = True
        t.start()

        def _listener(sample: zenoh.Sample):
            topic = sample.key_expr
            message = sample.payload

            # Uncover from brefv envelope
            received_at, enclosed_at, payload = brefv.uncover(message)

            # If this topic is known
            if topic in channels:
                queue.put((channels[topic], received_at, enclosed_at, payload))
                return

            try:
                tag = brefv.get_tag_from_topic(topic)
            except ValueError:
                logger.exception(
                    "Received topic did not match the expected format: %s", topic
                )

            brefv_encoding = brefv.get_tag_encoding(tag)
            brefv_description = brefv.get_tag_description(tag)

            # IF we havent already got a schema for this tag
            if not tag in schemas:
                # Either a brefv well-known tag
                if brefv.tag_is_well_known(tag):
                    schema_encoding = BREFV_TO_MCAP_SCHEMA_ENCODING_MAP.get(
                        brefv_encoding, SchemaEncoding.SelfDescribing
                    )
                    match brefv_encoding:
                        case "protobuf":
                            file_descriptor_set = (
                                brefv.get_protobuf_file_descriptor_set_from_type_name(
                                    brefv_description
                                )
                            )
                            schema = file_descriptor_set.SerializeToString()
                        case "json":
                            schema = brefv_description.encode()

                        case _:
                            logger.error(
                                "Brefv tag encoding: %s is not supported! Storing as raw bytes.",
                                brefv_encoding,
                            )
                            schema = b""

                else:
                    schema_encoding = SchemaEncoding.SelfDescribing
                    schema = b""

                writer.register_schema(name=tag, encoding=schema_encoding, data=schema)

            # Now we have a schema_id, moving on to registering a channel
            message_encoding = BREFV_TO_MCAP_MESSAGE_ENCODING_MAP.get(brefv_encoding)
            channels[topic] = writer.register_channel(
                topic=topic, message_encoding=message_encoding, schema_id=schemas[tag]
            )

            # Finally, put the sample on the queue
            queue.put((channels[topic], received_at, enclosed_at, payload))

        # And start subscribing
        subscribers = [session.declare_subscriber(key, _listener) for key in args.key]

        while True:
            try:
                time.sleep(0.1)
            except KeyboardInterrupt:
                for sub in subscribers:
                    sub.undeclare()
                while not queue.empty():
                    time.sleep(0.01)
                writer.finish()
                break


def main():
    parser = argparse.ArgumentParser(
        prog="record",
        description="A pure python recorder for keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--log-level", type=int, default=logging.INFO)

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

    ## Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)
    warnings.filterwarnings("once")
    zenoh.init_logger()

    # Put together zenoh session configuration
    conf = zenoh.Config()
    conf.insert_json5(zenoh.config.MODE_KEY, json.dumps("peer"))

    ## Construct session
    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)

    def _on_exit():
        session.close()

    atexit.register(_on_exit)

    run(session, args)


if __name__ == "__main__":
    main()
