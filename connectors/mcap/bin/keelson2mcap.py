#!/usr/bin/env python3

import os
import re
import sys
import json
import time
import atexit
import signal
import logging
import pathlib
import argparse
import shutil
from datetime import datetime, timezone
from queue import Queue, Empty
from threading import Thread, Event, Lock
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from contextlib import contextmanager
from collections import Counter

import zenoh
from google.protobuf.message import DecodeError
from mcap.writer import Writer
from mcap.well_known import MessageEncoding, SchemaEncoding

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedString
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    check_queue_backpressure,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    make_configurable,
    setup_logging,
    suppress_exception,
)

logger = logging.getLogger("mcap-record")

MAIN_LOOP_SLEEP_TIME = 0.5
FREQUENCY_DISPLAY_INTERVAL = 10.0

# Safeguards
SAFEGUARD_CHECK_INTERVAL = 5.0
MIN_FREE_DISK_PERCENT = 10.0
CPU_RESERVE_CORES = 1.0
CPU_OVERLOAD_GRACE_PERIOD = 15.0
RECORDER_NICE_INCREMENT = 10

# Name of the MCAP metadata record that carries the active key set.
KEY_SET_METADATA_NAME = "keelson2mcap.key_set"

# The recorder serves configurable/v1, and make_configurable republishes every
# applied key set on this subject, so it is the one subject the recorder
# declares a subject-level liveliness token for.
CONFIGURATION_SUBJECT = "configuration_json"


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


# The key set: what the recorder subscribes to, and what it refuses to write.
#
# Zenoh key expressions have no negation, so "everything under my entity except
# the container logs" cannot be said with -k alone. The key set pairs the
# subscriptions (`keys`) with exclusions (`exclude_keys`) that are applied to
# every sample before it is written. It is set from the command line at startup
# and can be replaced over configurable/v1 while recording, because the reason
# to exclude something (credentials turning up in a log stream) is usually
# discovered mid-capture, and a restart would rotate the file it is protecting.


def _key_expr_list(value: Any, name: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{name}' must be a list of key expression strings")
    keys: List[str] = []
    for key in value:
        try:
            zenoh.KeyExpr(key)
        except Exception as exc:
            # zenoh appends " at <rust source path>"; the reason is before it.
            reason = str(exc).split(" at /", 1)[0]
            raise ValueError(
                f"'{name}' entry {key!r} is not a valid key expression: {reason}"
            ) from None
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def validate_key_set(doc: Any) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Validate a key-set document, returning (keys, exclude_keys).

    The document is ``{"keys": [...], "exclude_keys": [...]}``. Raises
    ValueError on anything else, so a rejected document can be reported as
    such and nothing is applied.
    """
    if not isinstance(doc, dict):
        raise ValueError("key set must be a JSON object with 'keys' and 'exclude_keys'")
    unknown = sorted(set(doc) - {"keys", "exclude_keys"})
    if unknown:
        raise ValueError(f"unknown field(s) in key set: {', '.join(unknown)}")
    if "keys" not in doc:
        raise ValueError("key set is missing 'keys'")
    keys = _key_expr_list(doc["keys"], "keys")
    if not keys:
        raise ValueError("'keys' must name at least one key expression")
    exclude_keys = _key_expr_list(doc.get("exclude_keys", []), "exclude_keys")
    return keys, exclude_keys


@dataclass(frozen=True)
class KeySetSnapshot:
    """One immutable version of the key set.

    The recorder thread takes a snapshot per sample, so a key set replaced
    mid-write never applies half-way.
    """

    keys: Tuple[str, ...]
    exclude_keys: Tuple[str, ...]
    generation: int
    source: str
    changed_at: float

    _key_exprs: Tuple[zenoh.KeyExpr, ...] = field(init=False, repr=False, compare=False)
    _exclude_exprs: Tuple[zenoh.KeyExpr, ...] = field(
        init=False, repr=False, compare=False
    )
    _admitted: Dict[str, bool] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_key_exprs", tuple(map(zenoh.KeyExpr, self.keys)))
        object.__setattr__(
            self, "_exclude_exprs", tuple(map(zenoh.KeyExpr, self.exclude_keys))
        )

    def admits(self, key: str) -> bool:
        """True if a sample on `key` should be written.

        It must fall under one of `keys` (a sample still queued from a key that
        was just removed is dropped too) and under none of `exclude_keys`.
        Memoised per snapshot: the recorder sees the same keys over and over.
        """
        try:
            return self._admitted[key]
        except KeyError:
            pass
        expr = zenoh.KeyExpr(key)
        admitted = any(k.intersects(expr) for k in self._key_exprs) and not any(
            x.intersects(expr) for x in self._exclude_exprs
        )
        self._admitted[key] = admitted
        return admitted

    def config(self) -> Dict[str, List[str]]:
        return {"keys": list(self.keys), "exclude_keys": list(self.exclude_keys)}

    def metadata(self) -> Dict[str, str]:
        """The MCAP metadata record for this version (values must be strings)."""
        return {
            "keys": json.dumps(list(self.keys)),
            "exclude_keys": json.dumps(list(self.exclude_keys)),
            "generation": str(self.generation),
            "source": self.source,
            "changed_at": datetime.fromtimestamp(
                self.changed_at, tz=timezone.utc
            ).isoformat(),
        }


class KeySet:
    """The live key set, and the subscriptions that implement its `keys`.

    `declare(key)` and `undeclare(handle)` are injected so this can be tested
    without a Zenoh session; the recorder binds them to
    ``session.declare_subscriber(key, queue.put)`` and ``handle.undeclare()``.
    """

    def __init__(
        self,
        keys: List[str],
        exclude_keys: List[str],
        declare: Callable[[str], Any],
        undeclare: Callable[[Any], None],
        reconfigurable: bool = True,
    ) -> None:
        keys_, exclude_keys_ = validate_key_set(
            {"keys": list(keys), "exclude_keys": list(exclude_keys)}
        )
        self.snapshot = KeySetSnapshot(
            keys=keys_,
            exclude_keys=exclude_keys_,
            generation=0,
            source="cli",
            changed_at=time.time(),
        )
        self.reconfigurable = reconfigurable
        self._declare = declare
        self._undeclare = undeclare
        self._handles: Dict[str, Any] = {}
        self._lock = Lock()

    def start(self) -> None:
        """Declare the subscriptions for the current `keys`."""
        with self._lock:
            self._reconcile(self.snapshot.keys)

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                with suppress_exception(Exception, context="undeclare subscriber"):
                    self._undeclare(handle)
            self._handles.clear()

    def get_config(self) -> Dict[str, List[str]]:
        return self.snapshot.config()

    def set_config(self, doc: Any) -> None:
        """Replace the key set. A rejected document changes nothing."""
        if not self.reconfigurable:
            raise PermissionError(
                "runtime reconfiguration is disabled on this recorder "
                "(started with --no-runtime-reconfiguration)"
            )
        keys, exclude_keys = validate_key_set(doc)
        with self._lock:
            current = self.snapshot
            if (keys, exclude_keys) == (current.keys, current.exclude_keys):
                return
            self._reconcile(keys)
            self.snapshot = KeySetSnapshot(
                keys=keys,
                exclude_keys=exclude_keys,
                generation=current.generation + 1,
                source="set_config",
                changed_at=time.time(),
            )
        logger.info(
            "Key set replaced (generation %d): keys=%s exclude_keys=%s",
            self.snapshot.generation,
            list(keys),
            list(exclude_keys),
        )

    def _reconcile(self, keys: Tuple[str, ...]) -> None:
        # Declare the new subscriptions before dropping any old one, and undo
        # them all if one fails, so a failure leaves the running set intact.
        added: Dict[str, Any] = {}
        try:
            for key in keys:
                if key not in self._handles:
                    added[key] = self._declare(key)
        except Exception:
            for handle in added.values():
                with suppress_exception(Exception, context="undeclare subscriber"):
                    self._undeclare(handle)
            raise
        for key in [k for k in self._handles if k not in keys]:
            handle = self._handles.pop(key)
            with suppress_exception(Exception, context="undeclare subscriber"):
                self._undeclare(handle)
        self._handles.update(added)


def parse_size(size_str: str) -> Optional[int]:
    """Parse a size string like '1GB', '500MB', '100KB' to bytes."""
    if size_str is None:
        return None

    size_str = size_str.strip().upper()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT]?B?)$", size_str)
    if not match:
        raise ValueError(
            f"Invalid size format: {size_str}. "
            "Use formats like '1GB', '500MB', '100KB'"
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


def _nearest_existing_path(path: pathlib.Path) -> pathlib.Path:
    """
    Return the nearest existing parent of `path`.
    Useful when checking a path that may not exist yet.
    """
    path = path.resolve()
    current = path

    while not current.exists():
        if current.parent == current:
            raise FileNotFoundError(f"Could not find an existing parent for: {path}")
        current = current.parent

    return current


def get_disk_free_percent(path: pathlib.Path) -> Tuple[float, int, int]:
    """
    Return (free_percent, free_bytes, total_bytes) for the filesystem containing `path`.
    """
    check_path = _nearest_existing_path(path)
    usage = shutil.disk_usage(check_path)
    if usage.total == 0:
        raise ValueError(
            f"Filesystem at {check_path} reports total size of 0 bytes "
            "(degenerate/virtual filesystem)."
        )
    free_percent = (usage.free / usage.total) * 100.0
    return free_percent, usage.free, usage.total


def get_cpu_safeguard_status(
    reserve_cores: float = CPU_RESERVE_CORES,
) -> Optional[dict]:
    """
    Return CPU safeguard status based on 1-minute system load average.

    On Linux/Unix, load average approximates runnable demand. If load1 exceeds
    (logical_cpus - reserve_cores), the machine is considered overloaded.

    Returns None on platforms without os.getloadavg().
    """
    if not hasattr(os, "getloadavg"):
        return None

    cpu_count = max(1, os.cpu_count() or 1)
    reserve_cores = max(0.0, reserve_cores)
    allowed_load = max(0.1, cpu_count - reserve_cores)

    load1, load5, load15 = os.getloadavg()
    return {
        "cpu_count": cpu_count,
        "allowed_load": allowed_load,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "overloaded": load1 >= allowed_load,
    }


def apply_polite_cpu_priority() -> None:
    """
    Best-effort reduction of this process priority so other services
    (e.g. sshd) get a better chance to run under CPU pressure.
    """
    try:
        os.nice(RECORDER_NICE_INCREMENT)
        logger.info(
            "Applied CPU niceness adjustment: +%d",
            RECORDER_NICE_INCREMENT,
        )
    except AttributeError:
        logger.debug("os.nice() not available on this platform")
    except OSError as exc:
        logger.warning("Failed to adjust process niceness: %s", exc)


@dataclass
class MCAPRotatingWriter:
    """
    MCAP writer with logrotate-compatible rotation support.

    Preserves schema and channel definitions across file rotations,
    re-registering them with new IDs for each new file. Metadata records set
    with `set_metadata` are likewise re-written at the start of every file, so
    each rotated file describes itself.
    """

    output_folder: pathlib.Path
    file_pattern: str
    rotate_when: Optional[str] = None
    rotate_interval: int = 1
    max_size_bytes: Optional[int] = None
    rotate_requested: Optional[Event] = None

    schema_defs: Dict[str, SchemaDefinition] = field(default_factory=dict)
    channel_defs: Dict[str, ChannelDefinition] = field(default_factory=dict)
    metadata_defs: Dict[str, Dict[str, str]] = field(default_factory=dict)

    _writer: Optional[Writer] = field(default=None, init=False, repr=False)
    _file_handle: Optional[object] = field(default=None, init=False, repr=False)
    _current_path: Optional[pathlib.Path] = field(default=None, init=False, repr=False)
    _schema_ids: Dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _channel_ids: Dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _bytes_written: int = field(default=0, init=False, repr=False)
    _rollover_at: Optional[float] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.rotate_when:
            self._compute_next_rollover()

    def _compute_next_rollover(self) -> None:
        if not self.rotate_when:
            return

        current_time = time.time()
        when_upper = self.rotate_when.upper()

        if when_upper == "S":
            interval_seconds = 1
        elif when_upper == "M":
            interval_seconds = 60
        elif when_upper == "H":
            interval_seconds = 60 * 60
        elif when_upper in ("D", "MIDNIGHT"):
            interval_seconds = 60 * 60 * 24
        elif when_upper.startswith("W"):
            interval_seconds = 60 * 60 * 24 * 7
        else:
            interval_seconds = 60 * 60

        self._rollover_at = current_time + (interval_seconds * self.rotate_interval)

        logger.debug(
            "Next time-based rollover scheduled for: %s",
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._rollover_at)),
        )

    def _generate_filename(self) -> pathlib.Path:
        """Generate a new filename based on the pattern."""
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

        self._schema_ids.clear()
        self._channel_ids.clear()
        self._bytes_written = 0

        for subject, schema_def in self.schema_defs.items():
            self._schema_ids[subject] = self._writer.register_schema(
                name=schema_def.name,
                encoding=schema_def.encoding,
                data=schema_def.data,
            )
            logger.debug(
                "Re-registered schema %s with id %s",
                subject,
                self._schema_ids[subject],
            )

        for key, channel_def in self.channel_defs.items():
            schema_id = self._schema_ids[channel_def.schema_subject]
            self._channel_ids[key] = self._writer.register_channel(
                topic=channel_def.topic,
                message_encoding=channel_def.message_encoding,
                schema_id=schema_id,
            )
            logger.debug(
                "Re-registered channel %s with id %s",
                key,
                self._channel_ids[key],
            )

        for name, data in self.metadata_defs.items():
            self._writer.add_metadata(name=name, data=data)

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
        if self.rotate_requested and self.rotate_requested.is_set():
            self.rotate_requested.clear()
            logger.info("Rotation triggered by SIGHUP signal")
            return True

        if self._rollover_at and time.time() >= self._rollover_at:
            logger.info("Rotation triggered by time threshold")
            return True

        if self.max_size_bytes and self._bytes_written >= self.max_size_bytes:
            logger.info(
                "Rotation triggered by size threshold (%d bytes >= %d)",
                self._bytes_written,
                self.max_size_bytes,
            )
            return True

        return False

    def set_metadata(self, name: str, data: Dict[str, str]) -> None:
        """Write a metadata record now, and again at the start of every later file.

        Only the latest record per name is carried over a rotation.
        """
        self.metadata_defs[name] = dict(data)
        if self._writer:
            self._writer.add_metadata(name=name, data=dict(data))
            logger.debug("Wrote metadata record %s", name)

    def ensure_schema(
        self,
        subject: str,
        name: str,
        encoding: str,
        data: bytes,
    ) -> int:
        """
        Ensure a schema is registered, storing its definition for future rotations.

        Returns the schema ID for the current file.
        """
        if subject not in self.schema_defs:
            self.schema_defs[subject] = SchemaDefinition(
                name=name,
                encoding=encoding,
                data=data,
            )
            self._schema_ids[subject] = self._writer.register_schema(
                name=name,
                encoding=encoding,
                data=data,
            )
            logger.debug(
                "Registered new schema %s with id %s",
                subject,
                self._schema_ids[subject],
            )

        return self._schema_ids[subject]

    def ensure_channel(
        self,
        key: str,
        topic: str,
        message_encoding: str,
        schema_subject: str,
    ) -> int:
        """
        Ensure a channel is registered, storing its definition for future rotations.

        Returns the channel ID for the current file.
        """
        if key not in self.channel_defs:
            self.channel_defs[key] = ChannelDefinition(
                topic=topic,
                message_encoding=message_encoding,
                schema_subject=schema_subject,
            )
            schema_id = self._schema_ids[schema_subject]
            self._channel_ids[key] = self._writer.register_channel(
                topic=topic,
                message_encoding=message_encoding,
                schema_id=schema_id,
            )
            logger.debug(
                "Registered new channel %s with id %s",
                key,
                self._channel_ids[key],
            )

        return self._channel_ids[key]

    def write_message(
        self,
        channel_id: int,
        log_time: int,
        publish_time: int,
        data: bytes,
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
        self._bytes_written += len(data) + 24


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="keelson2mcap",
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
        "-x",
        "--exclude-key",
        type=str,
        action="append",
        default=[],
        help=(
            "Key expressions never to write, even when a --key matches them "
            "(e.g. the log_message subjects). Can be replaced at runtime over "
            "configurable/v1."
        ),
    )

    parser.add_argument("-r", "--realm", default="rise", help="Keelson realm")
    parser.add_argument(
        "-e",
        "--entity-id",
        default="keelson",
        help="Entity (recorder) ID, used to address its configurable/v1 interface",
    )
    parser.add_argument("-s", "--source-id", default="0", help="Source ID")

    parser.add_argument(
        "--runtime-reconfiguration",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Accept set_config over configurable/v1 to replace the recorded key "
            "set while recording. With --no-runtime-reconfiguration, get_config "
            "still answers and set_config is refused."
        ),
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
            "information about date and time of the recording."
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
        help="Display average receive frequencies periodically",
    )

    def _parse_pair(arg: str) -> Tuple[pathlib.Path, Optional[pathlib.Path]]:
        path_to_subject_yaml, path_to_proto_types = arg.split(",", 1)
        return pathlib.Path(path_to_subject_yaml), (
            pathlib.Path(path_to_proto_types) if path_to_proto_types else None
        )

    parser.add_argument(
        "--extra-subjects-types",
        type=_parse_pair,
        action="append",
        help=(
            "Add additional well-known subjects and protobuf types as "
            "--extra-subjects-types=path/to/subjects.yaml,"
            "path_to_protobuf_file_descriptor_set.bin"
        ),
    )

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
        help=(
            "Multiplier for --rotate-when "
            "(e.g., --rotate-when=H --rotate-interval=2 rotates every 2 hours)"
        ),
    )

    parser.add_argument(
        "--rotate-size",
        type=str,
        default=None,
        help=(
            "Size-based rotation threshold "
            "(e.g., '1GB', '500MB', '100KB'). Rotates when file exceeds this size."
        ),
    )

    parser.add_argument(
        "--pid-file",
        type=pathlib.Path,
        default=None,
        help="Write PID to this file for logrotate scripts to send SIGHUP signals.",
    )

    parser.add_argument(
        "--bypass-safeguards",
        action="store_true",
        help="Disable disk-space and CPU safeguards.",
    )

    args = parser.parse_args()

    try:
        validate_key_set({"keys": args.key, "exclude_keys": args.exclude_key})
    except ValueError as exc:
        parser.error(str(exc))

    setup_logging(level=args.log_level)

    if extra_paths := args.extra_subjects_types:
        for pair in extra_paths:
            logger.info("Loading extra subjects (%s) and types (%s)", *pair)
            keelson.add_well_known_subjects_and_proto_definitions(*pair)

    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)

    def _on_exit() -> None:
        session.close()

    atexit.register(_on_exit)

    run(session, args)


@contextmanager
def mcap_writer(file_handle):
    try:
        writer = Writer(file_handle)
        writer.start()
        logger.info("MCAP writer initialized")
        yield writer
    finally:
        writer.finish()
        logger.info("MCAP writer finished")


def write_message(
    writer: Writer,
    channel_id: int,
    log_time: int,
    publish_time: int,
    data: bytes,
) -> None:
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


def run(session: zenoh.Session, args: argparse.Namespace) -> None:
    if args.pid_file:
        pid_file = args.pid_file
        try:
            pid_file.write_text(str(os.getpid()))
            logger.info("Wrote PID %d to %s", os.getpid(), pid_file)
        except Exception as exc:
            logger.error("Failed to write PID file %s: %s", pid_file, exc)

        def _cleanup_pid_file() -> None:
            try:
                if pid_file.exists():
                    pid_file.unlink()
                    logger.debug("Removed PID file %s", pid_file)
            except Exception as exc:
                logger.warning("Failed to remove PID file %s: %s", pid_file, exc)

        atexit.register(_cleanup_pid_file)

    max_size_bytes = parse_size(args.rotate_size) if args.rotate_size else None

    rotation_enabled = args.rotate_when or max_size_bytes
    if rotation_enabled:
        logger.info(
            "Rotation enabled: when=%s, interval=%d, max_size=%s",
            args.rotate_when,
            args.rotate_interval,
            args.rotate_size,
        )

    queue = Queue()
    message_counter = Counter()
    suppressed_counter = Counter()

    key_set = KeySet(
        args.key,
        args.exclude_key,
        declare=lambda key: session.declare_subscriber(key, queue.put),
        undeclare=lambda subscriber: subscriber.undeclare(),
        reconfigurable=args.runtime_reconfiguration,
    )

    rotate_requested = Event()
    fatal_stop = Event()
    fatal_reason = {"message": None}

    def trigger_fatal_stop(message: str) -> None:
        if not fatal_stop.is_set():
            fatal_reason["message"] = message
            logger.critical(message)
            fatal_stop.set()

    custom_handlers = {}
    if hasattr(signal, "SIGHUP"):
        custom_handlers[signal.SIGHUP] = rotate_requested.set

    if args.bypass_safeguards:
        logger.warning("Safeguards are DISABLED via --bypass-safeguards")
    else:
        apply_polite_cpu_priority()
        logger.info(
            "Safeguards enabled: min_free_disk=%.1f%%, CPU reserve=%.1f core(s), "
            "overload grace=%.1fs",
            MIN_FREE_DISK_PERCENT,
            CPU_RESERVE_CORES,
            CPU_OVERLOAD_GRACE_PERIOD,
        )

    # Serving configurable/v1 gives the recorder a producing role: a source
    # token plus the configuration_json subject token here, the interface token
    # from serve_rpc (via make_configurable).
    with (
        GracefulShutdown(custom_handlers=custom_handlers) as shutdown,
        declare_liveliness(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            pubsub_subjects=[CONFIGURATION_SUBJECT],
        ),
    ):

        def _recorder() -> None:
            writer = MCAPRotatingWriter(
                output_folder=args.output_folder,
                file_pattern=args.file_name,
                rotate_when=args.rotate_when,
                rotate_interval=args.rotate_interval,
                max_size_bytes=max_size_bytes,
                rotate_requested=rotate_requested,
            )
            writer.open()

            def _process_sample(sample: zenoh.Sample, snap: KeySetSnapshot) -> None:
                key = str(sample.key_expr)
                logger.debug("Received sample on key: %s", key)

                if not snap.admits(key):
                    suppressed_counter[key] += 1
                    return

                message_counter[key] += 1

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
                    return

                if key in writer.channel_defs:
                    logger.debug("Key %s is already known!", key)
                    channel_id = writer._channel_ids[key]
                    writer.write_message(channel_id, received_at, enclosed_at, payload)
                    return

                try:
                    subject = keelson.get_subject_from_pubsub_key(key)
                except ValueError:
                    logger.exception(
                        "Received key did not match the expected format: %s",
                        key,
                    )
                    return

                logger.info("Unseen key %s, adding to file", key)

                if subject not in writer.schema_defs:
                    logger.debug("Subject %s not seen before", subject)

                    if keelson.is_subject_well_known(subject):
                        logger.info("Subject %s is well-known!", subject)
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

                logger.debug("...and writing the actual message to file!")
                writer.write_message(channel_id, received_at, enclosed_at, payload)

            # The key set in force, written into the file before any sample it
            # governs: a reader can then tell a subject that is absent because
            # it was excluded from one that is absent because it went quiet.
            written_generation = None

            def _current_key_set() -> KeySetSnapshot:
                nonlocal written_generation
                snap = key_set.snapshot
                if snap.generation != written_generation:
                    writer.set_metadata(KEY_SET_METADATA_NAME, snap.metadata())
                    written_generation = snap.generation
                return snap

            try:
                while not shutdown.is_requested() and not fatal_stop.is_set():
                    if writer.should_rotate():
                        writer.rotate()

                    snap = _current_key_set()

                    try:
                        sample = queue.get(timeout=0.01)
                    except Empty:
                        continue

                    with suppress_exception(Exception, context="recorder"):
                        _process_sample(sample, snap)

                if not fatal_stop.is_set():
                    logger.debug("Draining remaining queue items...")
                    while True:
                        try:
                            sample = queue.get_nowait()
                        except Empty:
                            break
                        with suppress_exception(Exception, context="recorder-drain"):
                            _process_sample(sample, _current_key_set())
                else:
                    logger.warning("Skipping queue drain because a safeguard triggered")
            finally:
                writer.close()

        recorder_thread = Thread(target=_recorder, daemon=True)
        recorder_thread.start()

        if args.query:
            logger.info("Querying the infrastructure for latest values!")

            def _receiver(reply: zenoh.Reply) -> None:
                with suppress_exception(Exception, context="recorder"):
                    queue.put(reply.ok)

            for key in args.key:
                session.get(
                    key,
                    _receiver,
                    consolidation=zenoh.ConsolidationMode.LATEST,
                )

        logger.info("Starting subscribers")
        key_set.start()
        if args.exclude_key:
            logger.info("Excluding from the recording: %s", args.exclude_key)

        configurable = make_configurable(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            key_set.get_config,
            key_set.set_config,
        )
        logger.info(
            "Serving configurable/v1 as %s/%s (set_config %s)",
            args.entity_id,
            args.source_id,
            "accepted" if args.runtime_reconfiguration else "refused",
        )

        # make_configurable republishes only after a set_config; publish the
        # starting key set once so a late joiner can see what is recorded.
        configuration_publisher = declare_publisher(
            session,
            keelson.construct_pubsub_key(
                args.realm, args.entity_id, CONFIGURATION_SUBJECT, args.source_id
            ),
        )
        initial_config = TimestampedString()
        initial_config.timestamp.FromNanoseconds(time.time_ns())
        initial_config.value = json.dumps(key_set.get_config())
        configuration_publisher.put(keelson.enclose(initial_config.SerializeToString()))

        last_freq_display = time.monotonic()
        last_safeguard_check = 0.0
        cpu_overload_since = None

        while not shutdown.is_requested() and not fatal_stop.is_set():
            check_queue_backpressure(queue, context="recorder")

            now = time.monotonic()

            if not args.bypass_safeguards:
                if (now - last_safeguard_check) >= SAFEGUARD_CHECK_INTERVAL:
                    last_safeguard_check = now

                    try:
                        free_percent, free_bytes, total_bytes = get_disk_free_percent(
                            args.output_folder
                        )
                        logger.debug(
                            "Disk free space at %s: %.2f%% (%.2f GiB / %.2f GiB)",
                            args.output_folder,
                            free_percent,
                            free_bytes / 1024**3,
                            total_bytes / 1024**3,
                        )
                        if free_percent < MIN_FREE_DISK_PERCENT:
                            trigger_fatal_stop(
                                "Disk safeguard triggered: free disk space on "
                                f"{args.output_folder} is {free_percent:.2f}% "
                                f"({free_bytes / 1024**3:.2f} GiB free of "
                                f"{total_bytes / 1024**3:.2f} GiB total), "
                                f"below the safety threshold of "
                                f"{MIN_FREE_DISK_PERCENT:.2f}%."
                            )
                    except Exception as exc:
                        logger.warning("Disk safeguard check failed: %s", exc)

                    try:
                        cpu_status = get_cpu_safeguard_status()
                        if cpu_status is not None:
                            if cpu_status["overloaded"]:
                                if cpu_overload_since is None:
                                    cpu_overload_since = now
                                    logger.warning(
                                        "CPU safeguard warning: system load is high "
                                        "(load1=%.2f, allowed=%.2f, cpus=%d). "
                                        "Will stop recorder if this persists for %.1f s.",
                                        cpu_status["load1"],
                                        cpu_status["allowed_load"],
                                        cpu_status["cpu_count"],
                                        CPU_OVERLOAD_GRACE_PERIOD,
                                    )
                                elif (
                                    now - cpu_overload_since
                                ) >= CPU_OVERLOAD_GRACE_PERIOD:
                                    trigger_fatal_stop(
                                        "CPU safeguard triggered: sustained high "
                                        "system load "
                                        f"(load1={cpu_status['load1']:.2f}, "
                                        f"allowed={cpu_status['allowed_load']:.2f}, "
                                        f"logical_cpus={cpu_status['cpu_count']}) "
                                        f"for at least "
                                        f"{CPU_OVERLOAD_GRACE_PERIOD:.1f} seconds."
                                    )
                            else:
                                if cpu_overload_since is not None:
                                    logger.info(
                                        "CPU safeguard recovered: load1=%.2f is back "
                                        "below allowed=%.2f",
                                        cpu_status["load1"],
                                        cpu_status["allowed_load"],
                                    )
                                cpu_overload_since = None
                    except Exception as exc:
                        logger.warning("CPU safeguard check failed: %s", exc)

            if (
                args.show_frequencies
                and (now - last_freq_display) >= FREQUENCY_DISPLAY_INTERVAL
            ):
                elapsed = now - last_freq_display
                to_print = [
                    f"Key: {key}, Frequency: {count / elapsed:.2f} Hz"
                    for key, count in message_counter.items()
                ]
                if to_print:
                    print(
                        "==== Average frequencies of received data over last "
                        f"{elapsed:.0f} s ====",
                        file=sys.stderr,
                    )
                    print("\n".join(to_print), file=sys.stderr)

                # Shown so an exclusion that matches nothing, or far too much,
                # is visible rather than assumed.
                suppressed = [
                    f"Key: {key}, Frequency: {count / elapsed:.2f} Hz"
                    for key, count in suppressed_counter.items()
                ]
                if suppressed:
                    print(
                        "==== Excluded (not written) over last "
                        f"{elapsed:.0f} s ====",
                        file=sys.stderr,
                    )
                    print("\n".join(suppressed), file=sys.stderr)

                message_counter.clear()
                suppressed_counter.clear()
                last_freq_display = now

            shutdown.wait(timeout=MAIN_LOOP_SLEEP_TIME)

        if fatal_stop.is_set():
            logger.critical(
                "Closing down due to safeguard: %s",
                fatal_reason["message"],
            )
        else:
            logger.info("Closing down on user request!")

        logger.debug("Undeclaring subscribers...")
        key_set.close()
        del configurable

        logger.debug("Joining recorder thread...")
        recorder_thread.join()

        logger.debug("Done! Good bye :)")

    if fatal_stop.is_set():
        sys.exit(1)


if __name__ == "__main__":
    main()
