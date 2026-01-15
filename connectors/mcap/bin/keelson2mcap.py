#!/usr/bin/env python3

import os
import re
import sys
import time
import atexit
import signal
import logging
import pathlib
import warnings
import argparse
from queue import Queue, Empty
from threading import Thread, Event
from typing import Dict, Optional
from dataclasses import dataclass, field
from contextlib import contextmanager


import zenoh
from mcap.writer import Writer
from mcap.well_known import SchemaEncoding, MessageEncoding
from google.protobuf.message import DecodeError

from collections import Counter

import keelson
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
)

logger = logging.getLogger("mcap-record")

MAIN_LOOP_SLEEP_TIME = 10.0  # seconds

# Global event for SIGHUP signal handling
rotate_requested = Event()


def handle_sighup(signum, frame):
    """Signal handler for SIGHUP - triggers rotation."""
    logger.info("Received SIGHUP, scheduling rotation...")
    rotate_requested.set()


# Register SIGHUP handler (Unix only)
if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, handle_sighup)


@dataclass
class SchemaDefinition:
    """Stores schema definition data that survives rotation."""

    name: str
    encoding: str
    data: bytes


@dataclass
class ChannelDefinition:
    """Stores channel definition data that survives rotation."""

    topic: str
    message_encoding: str
    schema_subject: str


def parse_size(size_str: str) -> int:
    """Parse a size string like '1GB', '500MB', '100KB' to bytes."""
    if size_str is None:
        return None

    size_str = size_str.strip().upper()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT]?B?)$", size_str)
    if not match:
        raise ValueError(
            f"Invalid size format: {size_str}. Use formats like '1GB', '500MB', '100KB'"
        )

    value = float(match.group(1))
    unit = match.group(2)

    multipliers = {
        "": 1,
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
    }

    return int(value * multipliers.get(unit, 1))


@dataclass
class MCAPRotatingWriter:
    """
    MCAP writer with logrotate-compatible rotation support.

    Preserves schema and channel definitions across file rotations,
    re-registering them with new IDs for each new file.
    """

    output_folder: pathlib.Path
    file_pattern: str
    rotate_when: Optional[str] = None
    rotate_interval: int = 1
    max_size_bytes: Optional[int] = None

    # Preserved state - survives rotation
    schema_defs: Dict[str, SchemaDefinition] = field(default_factory=dict)
    channel_defs: Dict[str, ChannelDefinition] = field(default_factory=dict)

    # Per-file state - reset on each rotation
    _writer: Optional[Writer] = field(default=None, init=False, repr=False)
    _file_handle: Optional[object] = field(default=None, init=False, repr=False)
    _current_path: Optional[pathlib.Path] = field(default=None, init=False, repr=False)
    _schema_ids: Dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _channel_ids: Dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _bytes_written: int = field(default=0, init=False, repr=False)
    _rollover_at: Optional[float] = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """Initialize rollover time if time-based rotation is configured."""
        if self.rotate_when:
            self._compute_next_rollover()

    def _compute_next_rollover(self) -> None:
        """Compute the next rollover time based on interval settings."""
        if not self.rotate_when:
            return

        current_time = time.time()

        # Calculate interval in seconds based on 'when' setting
        when_upper = self.rotate_when.upper()
        if when_upper == "S":
            interval_seconds = 1
        elif when_upper == "M":
            interval_seconds = 60
        elif when_upper == "H":
            interval_seconds = 60 * 60
        elif when_upper == "D" or when_upper == "MIDNIGHT":
            interval_seconds = 60 * 60 * 24
        elif when_upper.startswith("W"):
            interval_seconds = 60 * 60 * 24 * 7
        else:
            interval_seconds = 60 * 60  # Default to hourly

        # Apply the interval multiplier
        self._rollover_at = current_time + (interval_seconds * self.rotate_interval)

        logger.debug(
            "Next time-based rollover scheduled for: %s",
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._rollover_at)),
        )

    def _generate_filename(self) -> pathlib.Path:
        """Generate a new filename based on the pattern.

        Uses datetime.strftime to support %f (microseconds) format specifier.
        """
        from datetime import datetime

        filename = datetime.now().strftime(self.file_pattern)
        return (self.output_folder / filename).with_suffix(".mcap")

    def open(self) -> None:
        """Open a new MCAP file and re-register all schemas/channels."""
        self._current_path = self._generate_filename()
        logger.info("Opening new MCAP file: %s", self._current_path)

        self._file_handle = self._current_path.open("wb")
        self._writer = Writer(self._file_handle)
        self._writer.start()

        # Reset per-file state
        self._schema_ids.clear()
        self._channel_ids.clear()
        self._bytes_written = 0

        # Re-register all preserved schemas
        for subject, schema_def in self.schema_defs.items():
            self._schema_ids[subject] = self._writer.register_schema(
                name=schema_def.name,
                encoding=schema_def.encoding,
                data=schema_def.data,
            )
            logger.debug(
                "Re-registered schema %s with id %s", subject, self._schema_ids[subject]
            )

        # Re-register all preserved channels
        for key, channel_def in self.channel_defs.items():
            schema_id = self._schema_ids[channel_def.schema_subject]
            self._channel_ids[key] = self._writer.register_channel(
                topic=channel_def.topic,
                message_encoding=channel_def.message_encoding,
                schema_id=schema_id,
            )
            logger.debug(
                "Re-registered channel %s with id %s", key, self._channel_ids[key]
            )

        logger.info(
            "MCAP writer initialized with %d schemas and %d channels",
            len(self._schema_ids),
            len(self._channel_ids),
        )

    def close(self) -> None:
        """Properly finalize and close the current MCAP file."""
        if self._writer:
            self._writer.finish()
            logger.info("MCAP writer finished")
        if self._file_handle:
            self._file_handle.close()
            logger.info("Closed MCAP file: %s", self._current_path)
        self._writer = None
        self._file_handle = None

    def rotate(self) -> None:
        """Close current file and open a new one, preserving all definitions."""
        logger.info("Rotating MCAP file...")
        start_time = time.monotonic()

        self.close()
        if self.rotate_when:
            self._compute_next_rollover()
        self.open()

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info("Rotation completed in %.1f ms", elapsed_ms)

    def should_rotate(self) -> bool:
        """Check if rotation should occur based on time, size, or signal."""
        # Check for SIGHUP signal
        if rotate_requested.is_set():
            rotate_requested.clear()
            logger.info("Rotation triggered by SIGHUP signal")
            return True

        # Check time-based rotation
        if self._rollover_at and time.time() >= self._rollover_at:
            logger.info("Rotation triggered by time threshold")
            return True

        # Check size-based rotation
        if self.max_size_bytes and self._bytes_written >= self.max_size_bytes:
            logger.info(
                "Rotation triggered by size threshold (%d bytes >= %d)",
                self._bytes_written,
                self.max_size_bytes,
            )
            return True

        return False

    def ensure_schema(self, subject: str, name: str, encoding: str, data: bytes) -> int:
        """
        Ensure a schema is registered, storing its definition for future rotations.

        Returns the schema ID for the current file.
        """
        if subject not in self.schema_defs:
            # Store definition for preservation across rotations
            self.schema_defs[subject] = SchemaDefinition(
                name=name, encoding=encoding, data=data
            )
            # Register in current file
            self._schema_ids[subject] = self._writer.register_schema(
                name=name, encoding=encoding, data=data
            )
            logger.debug(
                "Registered new schema %s with id %s",
                subject,
                self._schema_ids[subject],
            )

        return self._schema_ids[subject]

    def ensure_channel(
        self, key: str, topic: str, message_encoding: str, schema_subject: str
    ) -> int:
        """
        Ensure a channel is registered, storing its definition for future rotations.

        Returns the channel ID for the current file.
        """
        if key not in self.channel_defs:
            # Store definition for preservation across rotations
            self.channel_defs[key] = ChannelDefinition(
                topic=topic,
                message_encoding=message_encoding,
                schema_subject=schema_subject,
            )
            # Register in current file
            schema_id = self._schema_ids[schema_subject]
            self._channel_ids[key] = self._writer.register_channel(
                topic=topic,
                message_encoding=message_encoding,
                schema_id=schema_id,
            )
            logger.debug(
                "Registered new channel %s with id %s", key, self._channel_ids[key]
            )

        return self._channel_ids[key]

    def write_message(
        self, channel_id: int, log_time: int, publish_time: int, data: bytes
    ) -> None:
        """Write a message to the current MCAP file."""
        logger.debug(
            "Writing to file: channel_id=%s, log_time=%s, publish_time=%s",
            channel_id,
            log_time,
            publish_time,
        )
        self._writer.add_message(
            channel_id=channel_id,
            log_time=log_time,
            publish_time=publish_time,
            data=data,
        )
        # Track approximate bytes written for size-based rotation
        self._bytes_written += len(data) + 24  # data + message header overhead


def main():
    parser = argparse.ArgumentParser(
        prog="mcap-record",
        description="A pure python mcap recorder for keelson",
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
        "--output-folder",
        type=pathlib.Path,
        required=True,
        help="Folder path where recordings will be stored.",
    )

    parser.add_argument(
        "--file-name",
        type=str,
        default="%Y-%m-%d_%H%M%S",
        help=(
            "File name of recording, will be given suffix '.mcap'. "
            "Format codes supported by `strftime` can be used to include "
            "information about date and time of the recording. "
        ),
    )

    parser.add_argument(
        "--query",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Query router storage for keys before subscribing to them",
    )

    parser.add_argument(
        "--show-frequencies",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Query router storage for keys before subscribing to them",
    )

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

    # Rotation arguments
    parser.add_argument(
        "--rotate-when",
        type=str,
        choices=[
            "S",
            "M",
            "H",
            "D",
            "midnight",
            "W0",
            "W1",
            "W2",
            "W3",
            "W4",
            "W5",
            "W6",
        ],
        default=None,
        help=(
            "Time-based rotation interval: S=seconds, M=minutes, H=hours, D=days, "
            "midnight=at midnight, W0-W6=weekly on day 0-6 (Monday=0). "
            "Use with --rotate-interval for multiplier."
        ),
    )

    parser.add_argument(
        "--rotate-interval",
        type=int,
        default=1,
        help="Multiplier for --rotate-when (e.g., --rotate-when=H --rotate-interval=2 rotates every 2 hours)",
    )

    parser.add_argument(
        "--rotate-size",
        type=str,
        default=None,
        help="Size-based rotation threshold (e.g., '1GB', '500MB', '100KB'). Rotates when file exceeds this size.",
    )

    parser.add_argument(
        "--pid-file",
        type=pathlib.Path,
        default=None,
        help="Write PID to this file for logrotate scripts to send SIGHUP signals.",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

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
    session = zenoh.open(conf)

    def _on_exit():
        session.close()

    atexit.register(_on_exit)

    run(session, args)


@contextmanager
def ignore(*exception):
    try:
        yield
    except exception as e:
        logger.exception("Something went wrong in the listener!", exc_info=e)


@contextmanager
def mcap_writer(file_handle):
    try:
        writer = Writer(file_handle)
        writer.start()
        logger.info("MCAP writer initilized")
        yield writer
    finally:
        writer.finish()
        logger.info("MCAP writer finished")


def write_message(
    writer: Writer, channel_id: int, log_time: int, publish_time: int, data: bytes
):
    logger.debug(
        "Writing to file: channel_id=%s, log_time=%s, publish_time=%s",
        channel_id,
        log_time,
        publish_time,
    )
    writer.add_message(
        channel_id=channel_id,
        log_time=log_time,
        publish_time=publish_time,
        data=data,
    )


def run(session: zenoh.Session, args: argparse.Namespace):
    # Write PID file if requested (for logrotate compatibility)
    if args.pid_file:
        pid_file = args.pid_file
        try:
            pid_file.write_text(str(os.getpid()))
            logger.info("Wrote PID %d to %s", os.getpid(), pid_file)
        except Exception as e:
            logger.error("Failed to write PID file %s: %s", pid_file, e)

        def _cleanup_pid_file():
            try:
                if pid_file.exists():
                    pid_file.unlink()
                    logger.debug("Removed PID file %s", pid_file)
            except Exception as e:
                logger.warning("Failed to remove PID file %s: %s", pid_file, e)

        atexit.register(_cleanup_pid_file)

    # Parse rotation size if provided
    max_size_bytes = parse_size(args.rotate_size) if args.rotate_size else None

    # Determine if rotation is enabled
    rotation_enabled = args.rotate_when or max_size_bytes
    if rotation_enabled:
        logger.info(
            "Rotation enabled: when=%s, interval=%d, max_size=%s",
            args.rotate_when,
            args.rotate_interval,
            args.rotate_size,
        )

    queue = Queue()
    close_down = Event()
    message_counter = Counter()

    def _recorder():
        # Create rotating writer
        writer = MCAPRotatingWriter(
            output_folder=args.output_folder,
            file_pattern=args.file_name,
            rotate_when=args.rotate_when,
            rotate_interval=args.rotate_interval,
            max_size_bytes=max_size_bytes,
        )
        writer.open()

        try:
            while not close_down.is_set():
                # Check for rotation (always check SIGHUP, or time/size if configured)
                if writer.should_rotate():
                    writer.rotate()

                try:
                    sample: zenoh.Sample = queue.get(timeout=0.01)
                except Empty:
                    continue

                with ignore(Exception):
                    key = str(sample.key_expr)
                    logger.debug("Received sample on key: %s", key)

                    # Increment message count for the topic
                    message_counter[key] += 1

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
                    if key in writer.channel_defs:
                        logger.debug("Key %s is already known!", key)
                        channel_id = writer._channel_ids[key]
                        writer.write_message(
                            channel_id, received_at, enclosed_at, payload
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

                    logger.info("Unseen key %s, adding to file", key)

                    # IF we havent already got a schema for this subject
                    if subject not in writer.schema_defs:
                        logger.debug("Subject %s not seen before", subject)

                        if keelson.is_subject_well_known(subject):
                            logger.info("Subject %s is well-known!", subject)
                            # Get info about the well-known subject
                            keelson_schema = keelson.get_subject_schema(subject)

                            file_descriptor_set = (
                                keelson.get_protobuf_file_descriptor_set_from_type_name(
                                    keelson_schema
                                )
                            )
                            writer.ensure_schema(
                                subject=subject,
                                name=keelson_schema,
                                encoding=SchemaEncoding.Protobuf,
                                data=file_descriptor_set.SerializeToString(),
                            )

                        else:
                            logger.info("Unknown subject, storing without schema...")
                            writer.ensure_schema(
                                subject=subject,
                                name=subject,
                                encoding=SchemaEncoding.SelfDescribing,
                                data=b"",
                            )

                    logger.debug(
                        "Registering a channel (%s) with subject=%s",
                        key,
                        subject,
                    )

                    channel_id = writer.ensure_channel(
                        key=key,
                        topic=key,
                        message_encoding=MessageEncoding.Protobuf,
                        schema_subject=subject,
                    )

                    # Finally, write the actual message to file
                    logger.debug("...and writing the actual message to file!")
                    writer.write_message(channel_id, received_at, enclosed_at, payload)
        finally:
            writer.close()

    recorder_thread = Thread(target=_recorder)
    recorder_thread.daemon = True
    recorder_thread.start()

    if args.query:
        logger.info("Querying the infrastructure for latest values!")

        def _receiver(reply: zenoh.Reply):
            with ignore(Exception):
                queue.put(reply.ok)

        for key in args.key:
            session.get(key, _receiver, consolidation=zenoh.ConsolidationMode.LATEST)

    # And start subscribing
    logger.info("Starting subscribers")
    subscribers = [session.declare_subscriber(key, queue.put) for key in args.key]

    while True:
        try:
            # Check queue size
            qsize = queue.qsize()
            logger.debug(f"Approximate queue size is: {qsize}")

            if qsize > 100:
                warnings.warn(f"Queue size is {qsize}")
            elif qsize > 1000:
                raise RuntimeError(
                    f"Recorder is not capable of keeping up with data flow. Current queue size is {qsize}. Exiting!"
                )

            if args.show_frequencies:
                to_print = [
                    f"Key: {key}, Frequency: {count / MAIN_LOOP_SLEEP_TIME:.2f} Hz"
                    for key, count in message_counter.items()
                ]
                if to_print:
                    print(
                        "==== Average frequencies of received data over last 10 s ===="
                    )
                    print("\n".join(to_print), file=sys.stderr)

            message_counter.clear()  # Reset counts after logging

            time.sleep(MAIN_LOOP_SLEEP_TIME)
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
            recorder_thread.join()

            logger.debug("Done! Good bye :)")
            break


if __name__ == "__main__":
    main()
