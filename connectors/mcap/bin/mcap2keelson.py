#!/usr/bin/env python3
"""MCAP replayer with Zenoh RPC control surface.

Runs as a stateful daemon: opens a Zenoh session, declares the
``McapReplayControl`` RPCs as queryables, broadcasts a ``replay_status``
envelope at 1 Hz, and walks an MCAP file's messages onto their original
Zenoh topics with timing preserved.

A separate replay thread owns the MCAP iterator. RPC callbacks mutate
shared state under ``STATE_LOCK`` and signal the replay thread via two
events (``PAUSE_EVENT``, ``COMMAND_EVENT``); the iterator is interrupted
between messages so seek/load/stop take effect within one tick.
"""

import argparse
import atexit
import logging
import pathlib
import threading
import time
import traceback
from typing import Any, Callable, NamedTuple, Optional

import zenoh
from mcap.reader import make_reader
from mcap.records import Channel, Message

import keelson
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
)
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.McapReplayControl_pb2 import (
    FileInfo,
    ListFilesRequest,
    ListFilesResponse,
    LoadFileRequest,
    McapReplaySuccessResponse,
)
from keelson.interfaces.McapReplayControl_pb2 import (
    ReplayStatus as RpcReplayStatus,
)
from keelson.interfaces.McapReplayControl_pb2 import (
    SeekRequest,
    SetLoopRequest,
    SetSpeedRequest,
)
from keelson.payloads.ReplayStatus_pb2 import ReplayStatus as PubReplayStatus

logger = logging.getLogger("mcap-replay")

REPLAY_STATUS_SUBJECT = "replay_status"
STATUS_PUBLISH_PERIOD_S = 1.0
SPEED_RANGE = (0.25, 4.0)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


class ReplayerState:
    """All mutable replayer state. Read and written under ``STATE_LOCK``."""

    def __init__(self) -> None:
        self.state: int = PubReplayStatus.STOPPED
        self.loaded_file: Optional[pathlib.Path] = None
        self.reader = None
        self.file_handle = None
        self.start_time_ns: int = 0
        self.end_time_ns: int = 0
        self.current_time_ns: int = 0
        self.total_message_count: int = 0
        self.played_message_count: int = 0
        self.playback_speed: float = 1.0
        self.loop: bool = False
        self.channel_count: int = 0
        self.publishers: dict[int, zenoh.Publisher] = {}
        self.seek_target_ns: Optional[int] = None


STATE = ReplayerState()
STATE_LOCK = threading.RLock()
PAUSE_EVENT = threading.Event()  # set = run, clear = pause
COMMAND_EVENT = threading.Event()  # set by RPC handlers to break the iterator


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def _close_publishers() -> None:
    """Undeclare cached publishers. Caller holds STATE_LOCK."""
    for pub in STATE.publishers.values():
        try:
            pub.undeclare()
        except Exception:
            logger.exception("Failed to undeclare publisher")
    STATE.publishers.clear()


def _close_reader() -> None:
    """Close the open MCAP file handle. Caller holds STATE_LOCK."""
    if STATE.file_handle is not None:
        try:
            STATE.file_handle.close()
        except Exception:
            logger.exception("Failed to close MCAP file handle")
    STATE.file_handle = None
    STATE.reader = None


def _declare_publishers_from_summary(
    session: zenoh.Session, summary, replay_key_tag: bool
) -> None:
    """Pre-declare a publisher per channel using the file's summary.
    Caller holds STATE_LOCK."""
    for channel_id, channel in summary.channels.items():
        topic = channel.topic + "/replay" if replay_key_tag else channel.topic
        logger.info("Declaring publisher for: %s", topic)
        STATE.publishers[channel_id] = session.declare_publisher(topic)


def _load_file(
    session: zenoh.Session, args: argparse.Namespace, path: pathlib.Path
) -> None:
    """Open an MCAP file and reset replay state. Acquires STATE_LOCK itself."""
    with STATE_LOCK:
        prior_state = STATE.state
        STATE.state = PubReplayStatus.LOADING
    COMMAND_EVENT.set()  # break the replay thread out of any active iterator

    fh = path.open("rb")
    try:
        reader = make_reader(fh)
        summary = None
        try:
            summary = reader.get_summary()
        except Exception:
            logger.warning(
                "MCAP footer/index unreadable for %s; stats unavailable", path
            )
    except Exception:
        fh.close()
        with STATE_LOCK:
            STATE.state = prior_state
        raise

    with STATE_LOCK:
        _close_publishers()
        _close_reader()

        STATE.file_handle = fh
        STATE.reader = reader
        STATE.loaded_file = path
        STATE.played_message_count = 0
        STATE.seek_target_ns = None

        if summary is not None and summary.statistics is not None:
            stats = summary.statistics
            STATE.start_time_ns = stats.message_start_time
            STATE.end_time_ns = stats.message_end_time
            STATE.current_time_ns = stats.message_start_time
            STATE.total_message_count = stats.message_count
            STATE.channel_count = stats.channel_count
        else:
            STATE.start_time_ns = 0
            STATE.end_time_ns = 0
            STATE.current_time_ns = 0
            STATE.total_message_count = 0
            STATE.channel_count = 0

        if summary is not None:
            _declare_publishers_from_summary(session, summary, args.replay_key_tag)

        STATE.state = PubReplayStatus.PAUSED
    PAUSE_EVENT.clear()
    logger.info("Loaded: %s (%d messages)", path, STATE.total_message_count)


# ---------------------------------------------------------------------------
# Replay loop
# ---------------------------------------------------------------------------


def _ensure_publisher(
    session: zenoh.Session, channel: Channel, replay_key_tag: bool
) -> zenoh.Publisher:
    """Return a publisher for ``channel.id``, declaring lazily if needed.
    Caller holds STATE_LOCK."""
    pub = STATE.publishers.get(channel.id)
    if pub is None:
        topic = channel.topic + "/replay" if replay_key_tag else channel.topic
        logger.info("Declaring publisher for: %s", topic)
        pub = session.declare_publisher(topic)
        STATE.publishers[channel.id] = pub
    return pub


def _emit(
    session: zenoh.Session, args: argparse.Namespace, channel: Channel, message: Message
) -> None:
    """Serialize and publish a single MCAP message."""
    with STATE_LOCK:
        pub = _ensure_publisher(session, channel, args.replay_key_tag)
    envelope = keelson.enclose(payload=message.data, enclosed_at=message.publish_time)
    pub.put(envelope)


def _walk_iterator(
    session: zenoh.Session, args: argparse.Namespace, shutdown: GracefulShutdown
) -> None:
    """Walk the current reader from ``seek_target_ns`` (or start) to EOF.

    Honors pause (blocks on PAUSE_EVENT), stop/seek/load (breaks on
    COMMAND_EVENT), and set_speed (re-reads ``playback_speed`` each tick).
    On EOF the caller decides whether to loop or transition to STOPPED.
    """
    COMMAND_EVENT.clear()

    with STATE_LOCK:
        reader = STATE.reader
        seek_target = STATE.seek_target_ns
        STATE.seek_target_ns = None
        start_ns = STATE.start_time_ns

    if reader is None:
        return

    # NOTE: MCAP's end_time is exclusive ("logged at or after ... not included"),
    # so omit it — we want every message through EOF. seek sets start_time only.
    iterator = reader.iter_messages(
        log_time_order=True,
        start_time=seek_target if seek_target is not None else (start_ns or None),
    )

    first = None
    reference_wall_ns = None
    seek_first_emit = seek_target is not None

    for _, channel, message in iterator:
        if shutdown.is_requested() or COMMAND_EVENT.is_set():
            return

        # Block while paused. Re-check stop/load/seek on wake.
        while not PAUSE_EVENT.is_set():
            if shutdown.is_requested() or COMMAND_EVENT.is_set():
                return
            PAUSE_EVENT.wait(timeout=0.2)

        with STATE_LOCK:
            speed = STATE.playback_speed

        current = message.log_time
        if first is None or seek_first_emit:
            first = current
            reference_wall_ns = time.time_ns()
            seek_first_emit = False
        else:
            lag = int((current - first) / max(speed, 1e-6))
            # Spin-wait for precise timing (matches the prior implementation).
            while (time.time_ns() - reference_wall_ns) < lag:
                if shutdown.is_requested() or COMMAND_EVENT.is_set():
                    return
                if not PAUSE_EVENT.is_set():
                    break
                time.sleep(0.0)

        _emit(session, args, channel, message)

        with STATE_LOCK:
            # Drop the increment if a STOP/LOAD raced with this emit — the
            # handler already reset the counters and the next loop iteration
            # will exit on COMMAND_EVENT anyway.
            if STATE.state == PubReplayStatus.PLAYING:
                STATE.current_time_ns = current
                STATE.played_message_count += 1


def _replay_loop(
    session: zenoh.Session, args: argparse.Namespace, shutdown: GracefulShutdown
) -> None:
    """Outer replay loop: idle in STOPPED/PAUSED, advance in PLAYING."""
    while not shutdown.is_requested():
        with STATE_LOCK:
            playing = (
                STATE.state == PubReplayStatus.PLAYING and STATE.reader is not None
            )
        if not playing:
            shutdown.wait(timeout=0.1)
            continue

        _walk_iterator(session, args, shutdown)

        # Why we got out: command requested, shutdown, or natural EOF.
        if shutdown.is_requested():
            return
        if COMMAND_EVENT.is_set():
            continue

        # EOF reached.
        with STATE_LOCK:
            loop = STATE.loop
            if loop:
                STATE.played_message_count = 0
                STATE.current_time_ns = STATE.start_time_ns
                STATE.seek_target_ns = None
                logger.info("End of file; looping")
            else:
                STATE.state = PubReplayStatus.STOPPED
                STATE.current_time_ns = STATE.end_time_ns
                logger.info("End of file; stopping")
                PAUSE_EVENT.set()  # leave the event "run" so next play starts cleanly


# ---------------------------------------------------------------------------
# Status publishing
# ---------------------------------------------------------------------------


def _build_pub_status() -> PubReplayStatus:
    """Snapshot STATE into a ReplayStatus pub payload. Caller holds STATE_LOCK."""
    msg = PubReplayStatus(
        state=STATE.state,
        playback_speed=STATE.playback_speed,
        loaded_file=str(STATE.loaded_file) if STATE.loaded_file is not None else "",
        total_message_count=STATE.total_message_count,
        played_message_count=STATE.played_message_count,
        loop=STATE.loop,
    )
    if STATE.start_time_ns:
        msg.start_time.FromNanoseconds(STATE.start_time_ns)
    if STATE.end_time_ns:
        msg.end_time.FromNanoseconds(STATE.end_time_ns)
    if STATE.current_time_ns:
        msg.current_time.FromNanoseconds(STATE.current_time_ns)
    if STATE.total_message_count:
        msg.progress_pct = (
            100.0 * STATE.played_message_count / STATE.total_message_count
        )
    return msg


def _build_rpc_status() -> RpcReplayStatus:
    """Snapshot STATE into a ReplayStatus RPC response. Caller holds STATE_LOCK."""
    msg = RpcReplayStatus(
        state=STATE.state,
        playback_speed=STATE.playback_speed,
        loaded_file=str(STATE.loaded_file) if STATE.loaded_file is not None else "",
        channel_count=STATE.channel_count,
        total_message_count=STATE.total_message_count,
        played_message_count=STATE.played_message_count,
        loop=STATE.loop,
    )
    if STATE.start_time_ns:
        msg.start_time.FromNanoseconds(STATE.start_time_ns)
    if STATE.end_time_ns:
        msg.end_time.FromNanoseconds(STATE.end_time_ns)
    if STATE.current_time_ns:
        msg.current_time.FromNanoseconds(STATE.current_time_ns)
    if STATE.total_message_count:
        msg.progress_pct = (
            100.0 * STATE.played_message_count / STATE.total_message_count
        )
    return msg


def _status_loop(
    session: zenoh.Session, args: argparse.Namespace, shutdown: GracefulShutdown
) -> None:
    key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, REPLAY_STATUS_SUBJECT, args.source_id
    )
    pub = session.declare_publisher(key)
    logger.info("Publishing replay status on: %s", key)
    try:
        while not shutdown.is_requested():
            with STATE_LOCK:
                payload = _build_pub_status().SerializeToString()
            try:
                pub.put(keelson.enclose(payload=payload))
            except Exception:
                logger.exception("Failed to publish replay_status")
            shutdown.wait(timeout=STATUS_PUBLISH_PERIOD_S)
    finally:
        try:
            pub.undeclare()
        except Exception:
            logger.exception("Failed to undeclare status publisher")


# ---------------------------------------------------------------------------
# RPC dispatch
# ---------------------------------------------------------------------------


class RpcOp(NamedTuple):
    query: Any
    procedure: str
    reply_key: str
    request_bytes: bytes


def _reply_err(query, msg: str) -> None:
    try:
        query.reply_err(ErrorResponse(error_description=msg).SerializeToString())
    except Exception:
        logger.exception("Failed to reply_err on RPC")


def _reply_ok(query, reply_key: str) -> None:
    query.reply(reply_key, McapReplaySuccessResponse().SerializeToString())


# ---- RPC handlers ----------------------------------------------------------


def _handle_get_status(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    with STATE_LOCK:
        payload = _build_rpc_status().SerializeToString()
    op.query.reply(op.reply_key, payload)


def _handle_play(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    with STATE_LOCK:
        if STATE.reader is None:
            return _reply_err(op.query, "no file loaded")
        if STATE.state == PubReplayStatus.STOPPED:
            # Restart from the beginning of the file.
            STATE.played_message_count = 0
            STATE.current_time_ns = STATE.start_time_ns
            STATE.seek_target_ns = None
        STATE.state = PubReplayStatus.PLAYING
    # Don't set COMMAND_EVENT here — the replay thread is idle in STOPPED/PAUSED
    # when play is called, so there's no iterator to interrupt. Setting it would
    # race against _walk_iterator's clear() and drop the next emit.
    PAUSE_EVENT.set()
    _reply_ok(op.query, op.reply_key)


def _handle_pause(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    with STATE_LOCK:
        if STATE.state != PubReplayStatus.PLAYING:
            return _reply_err(
                op.query,
                f"cannot pause in state {PubReplayStatus.State.Name(STATE.state)}",
            )
        STATE.state = PubReplayStatus.PAUSED
    PAUSE_EVENT.clear()
    _reply_ok(op.query, op.reply_key)


def _handle_stop(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    with STATE_LOCK:
        STATE.state = PubReplayStatus.STOPPED
        STATE.played_message_count = 0
        STATE.current_time_ns = STATE.start_time_ns
        STATE.seek_target_ns = None
    PAUSE_EVENT.set()
    COMMAND_EVENT.set()
    _reply_ok(op.query, op.reply_key)


def _handle_seek(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    req = SeekRequest()
    req.ParseFromString(op.request_bytes)
    target_ns = req.target.ToNanoseconds()
    with STATE_LOCK:
        if STATE.reader is None:
            return _reply_err(op.query, "no file loaded")
        if not (STATE.start_time_ns <= target_ns <= STATE.end_time_ns):
            return _reply_err(
                op.query,
                f"seek target {target_ns} out of range "
                f"[{STATE.start_time_ns}, {STATE.end_time_ns}]",
            )
        STATE.seek_target_ns = target_ns
        STATE.current_time_ns = target_ns
    COMMAND_EVENT.set()
    _reply_ok(op.query, op.reply_key)


def _handle_set_speed(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = SetSpeedRequest()
    req.ParseFromString(op.request_bytes)
    lo, hi = SPEED_RANGE
    if not (lo <= req.speed <= hi):
        return _reply_err(op.query, f"speed {req.speed} out of range [{lo}, {hi}]")
    with STATE_LOCK:
        STATE.playback_speed = req.speed
    _reply_ok(op.query, op.reply_key)


def _handle_set_loop(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = SetLoopRequest()
    req.ParseFromString(op.request_bytes)
    with STATE_LOCK:
        STATE.loop = req.loop
    _reply_ok(op.query, op.reply_key)


def _handle_list_files(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = ListFilesRequest()
    req.ParseFromString(op.request_bytes)
    pattern = req.pattern or "*.mcap"
    base = args.base_directory.resolve()
    resp = ListFilesResponse(base_directory=str(base))
    for path in sorted(base.glob(pattern)):
        if not path.is_file():
            continue
        info = FileInfo(
            path=str(path.relative_to(base)),
            size_bytes=path.stat().st_size,
        )
        try:
            with path.open("rb") as fh:
                summary = make_reader(fh).get_summary()
                if summary and summary.statistics:
                    info.message_count = summary.statistics.message_count
                    info.channel_count = summary.statistics.channel_count
                    info.start_time.FromNanoseconds(
                        summary.statistics.message_start_time
                    )
                    info.end_time.FromNanoseconds(summary.statistics.message_end_time)
                    info.channel_names.extend(
                        c.topic for c in summary.channels.values()
                    )
        except Exception:
            logger.warning("Failed to read summary for %s", path, exc_info=True)
        resp.files.append(info)
    op.query.reply(op.reply_key, resp.SerializeToString())


def _handle_load_file(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = LoadFileRequest()
    req.ParseFromString(op.request_bytes)
    candidate = pathlib.Path(req.path)
    path = (
        candidate if candidate.is_absolute() else args.base_directory / candidate
    ).resolve()
    base = args.base_directory.resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return _reply_err(op.query, f"path escapes base directory: {req.path}")
    if not path.is_file():
        return _reply_err(op.query, f"file not found: {req.path}")
    try:
        _load_file(session, args, path)
    except Exception as exc:
        with STATE_LOCK:
            STATE.state = PubReplayStatus.STOPPED
        return _reply_err(op.query, f"load failed: {exc}")
    _reply_ok(op.query, op.reply_key)


_RPC_HANDLERS: dict[str, Callable[[zenoh.Session, argparse.Namespace, RpcOp], None]] = {
    "get_status": _handle_get_status,
    "list_files": _handle_list_files,
    "load_file": _handle_load_file,
    "play": _handle_play,
    "pause": _handle_pause,
    "stop": _handle_stop,
    "seek": _handle_seek,
    "set_speed": _handle_set_speed,
    "set_loop": _handle_set_loop,
}


def _make_rpc_handler(
    procedure: str, reply_key: str, session: zenoh.Session, args: argparse.Namespace
):
    handler = _RPC_HANDLERS[procedure]

    def _callback(query) -> None:
        try:
            payload = query.payload
            request_bytes = bytes(payload.to_bytes()) if payload is not None else b""
        except Exception:
            request_bytes = b""
        op = RpcOp(
            query=query,
            procedure=procedure,
            reply_key=reply_key,
            request_bytes=request_bytes,
        )
        try:
            handler(session, args, op)
        except Exception:
            logger.exception("RPC %s handler failed", procedure)
            _reply_err(query, traceback.format_exc())

    return _callback


def _setup_rpc_queryables(session: zenoh.Session, args: argparse.Namespace) -> list:
    queryables = []
    for proc in _RPC_HANDLERS:
        key = keelson.construct_rpc_key(
            args.realm, args.entity_id, proc, args.source_id
        )
        q = session.declare_queryable(
            key, _make_rpc_handler(proc, key, session, args), complete=True
        )
        logger.info("Declared RPC queryable: %s", key)
        queryables.append(q)
    return queryables


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcap2keelson",
        description=(
            "Stateful MCAP replayer with Zenoh RPC control. Serves the "
            "McapReplayControl interface and broadcasts replay_status at 1 Hz."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", default="rise", help="Keelson realm")
    parser.add_argument(
        "-e", "--entity-id", default="keelson", help="Entity (replayer) ID"
    )
    parser.add_argument("-s", "--source-id", default="0", help="Source ID")

    parser.add_argument(
        "--base-directory",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="Directory served by list_files and resolved against load_file paths",
    )

    parser.add_argument(
        "-mf",
        "--mcap-file",
        type=pathlib.Path,
        help=(
            "If set, load this file at startup and begin playing "
            "(unless --start-paused). Absolute path or relative to --base-directory."
        ),
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="Initial loop setting; changeable at runtime via set_loop RPC",
    )

    parser.add_argument(
        "--start-paused",
        action="store_true",
        help="When --mcap-file is given, load but start PAUSED instead of PLAYING",
    )

    parser.add_argument(
        "--replay-key-tag",
        action="store_true",
        help="Append '/replay' suffix to published topic keys",
    )

    return parser


def main() -> None:
    args = _build_parser().parse_args()

    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    logger.info("Starting mcap-replay daemon (Ctrl-C to stop)")

    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)
    session = zenoh.open(conf)
    atexit.register(lambda: _safe_close(session))

    shutdown = GracefulShutdown()

    with (
        shutdown,
        declare_liveliness_token(session, args.realm, args.entity_id, args.source_id),
    ):
        with STATE_LOCK:
            STATE.loop = args.loop
        PAUSE_EVENT.set()  # default to running (gated by STATE.state)

        queryables = _setup_rpc_queryables(session, args)

        if args.mcap_file is not None:
            candidate = args.mcap_file
            initial_path = (
                candidate
                if candidate.is_absolute()
                else (args.base_directory / candidate)
            ).resolve()
            _load_file(session, args, initial_path)
            with STATE_LOCK:
                STATE.state = (
                    PubReplayStatus.PAUSED
                    if args.start_paused
                    else PubReplayStatus.PLAYING
                )
            # _load_file leaves PAUSE_EVENT cleared (state=PAUSED). If we're
            # starting in PLAYING, re-arm the event so the replay thread
            # doesn't block on the pause-wait at the first message.
            if not args.start_paused:
                PAUSE_EVENT.set()

        replay_t = threading.Thread(
            target=_replay_loop,
            args=(session, args, shutdown),
            name="mcap-replay",
            daemon=True,
        )
        status_t = threading.Thread(
            target=_status_loop,
            args=(session, args, shutdown),
            name="mcap-replay-status",
            daemon=True,
        )
        replay_t.start()
        status_t.start()

        while not shutdown.is_requested():
            shutdown.wait(timeout=1.0)

        logger.info("Shutting down")

        # Wake the replay thread out of any pause / spin so it can exit.
        PAUSE_EVENT.set()
        COMMAND_EVENT.set()

        for q in queryables:
            try:
                q.undeclare()
            except Exception:
                logger.exception("Failed to undeclare queryable")

        replay_t.join(timeout=2.0)
        status_t.join(timeout=2.0)

        with STATE_LOCK:
            _close_publishers()
            _close_reader()


def _safe_close(session: zenoh.Session) -> None:
    try:
        session.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
