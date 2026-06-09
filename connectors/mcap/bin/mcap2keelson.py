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
import importlib.metadata
import logging
import pathlib
import socket
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
    SetChannelFilterRequest,
    SetLoopRequest,
    SetSegmentRequest,
    SetSpeedRequest,
    StepRequest,
)
from keelson.payloads.ReplayStatus_pb2 import ReplayStatus as PubReplayStatus

logger = logging.getLogger("mcap-replay")

REPLAY_STATUS_SUBJECT = "replay_status"
# Variable broadcast cadence: 5 Hz while PLAYING (smooth scrubber/counters),
# 1 Hz otherwise. RPC mutators also fire an immediate sample so the client
# sees state changes within one network round-trip rather than one period.
STATUS_PERIOD_PLAYING_S = 0.2
STATUS_PERIOD_IDLE_S = 1.0
SPEED_RANGE = (0.25, 4.0)

# Module-level handle to the running status publisher. Set by `_status_loop`
# while it owns the publisher; cleared in the loop's finally. Read by
# `_publish_status_now()` from RPC threads to emit immediate state updates.
_STATUS_PUBLISHER: Optional["zenoh.Publisher"] = None

# Static daemon identification, populated once at startup. Read on every
# status sample. Tuple of (version, hostname, started_at_ns, base_directory).
_DAEMON_INFO: Optional[tuple[str, str, int, str]] = None


def _init_daemon_info(args: argparse.Namespace) -> None:
    global _DAEMON_INFO
    try:
        version = importlib.metadata.version("keelson")
    except Exception:
        version = "unknown"
    _DAEMON_INFO = (
        version,
        socket.gethostname(),
        time.time_ns(),
        str(args.base_directory),
    )


def _fill_daemon_info(msg) -> None:
    """Populate the `daemon` sub-field on a ReplayStatus (pub or RPC)."""
    if _DAEMON_INFO is None:
        return
    version, hostname, started_at_ns, base_directory = _DAEMON_INFO
    msg.daemon.version = version
    msg.daemon.hostname = hostname
    msg.daemon.started_at.FromNanoseconds(started_at_ns)
    msg.daemon.base_directory = base_directory


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
        # When > 0, the replay thread is in "step" mode: it bypasses timing,
        # emits one message per tick, decrements this counter, and transitions
        # back to PAUSED when it reaches zero. Set by _handle_step.
        self.step_remaining: int = 0
        # Optional A-B segment: when set, _walk_iterator starts from
        # segment_start and treats segment_end as EOF; looping reseeks to
        # segment_start instead of file start. Both None = whole-file.
        self.segment_start_ns: Optional[int] = None
        self.segment_end_ns: Optional[int] = None
        # Channel allowlist: when not None, _emit drops messages whose
        # channel topic isn't in the set. Counter still advances so progress
        # is monotonic across the file.
        self.channel_filter: Optional[set[str]] = None
        # 0..100 while LOADING (set by the load worker); 0 otherwise.
        self.load_progress_pct: float = 0.0
        # Description of the most-recent load_file failure, or "" if the last
        # load succeeded. Surfaced through replay_status so async failures
        # reach clients that have already received the OK on load_file.
        self.last_load_error: str = ""


STATE = ReplayerState()
STATE_LOCK = threading.RLock()
PAUSE_EVENT = threading.Event()  # set = run, clear = pause
COMMAND_EVENT = threading.Event()  # set by RPC handlers to break the iterator
# Serializes load_file workers so a second load can't race the first half-way
# through. New loads queue behind the active one rather than corrupting state.
_LOAD_LOCK = threading.Lock()


def _set_state(new_state: int, reason: str) -> None:
    """Mutate STATE.state, log the transition. Caller holds STATE_LOCK.

    Reads as the single point of truth for state changes — every state-altering
    code path should go through here so the operator-visible audit log under
    the ``[STATE]`` prefix stays complete.
    """
    old = STATE.state
    if old == new_state:
        return  # no-op transition; don't spam the log
    STATE.state = new_state
    logger.info(
        "[STATE] %s -> %s (reason=%s)",
        PubReplayStatus.State.Name(old),
        PubReplayStatus.State.Name(new_state),
        reason,
    )


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
    count = 0
    for channel_id, channel in summary.channels.items():
        topic = channel.topic + "/replay" if replay_key_tag else channel.topic
        logger.debug("[LOAD] declaring publisher: %s", topic)
        STATE.publishers[channel_id] = session.declare_publisher(topic)
        count += 1
    logger.info("[LOAD] declared %d publishers", count)


def _set_load_progress(pct: float) -> None:
    """Update load_progress_pct under the lock and broadcast immediately."""
    with STATE_LOCK:
        STATE.load_progress_pct = max(0.0, min(100.0, pct))
    _publish_status_now()


def _scan_file(reader) -> tuple[int, int, int, set[str]]:
    """Recover (start_ns, end_ns, count, topics) for files whose summary lacks
    statistics, so seek/segment/progress work instead of silently degrading.

    iter_messages(log_time_order=False) streams in file order with no in-memory
    sort; min/max are order-independent. Safe to call before a walk: the mcap
    SeekingReader re-seeks to the start on every iter_messages call.
    """
    first_ns: Optional[int] = None
    last_ns: Optional[int] = None
    count = 0
    topics: set[str] = set()
    for _, channel, message in reader.iter_messages(log_time_order=False):
        t = message.log_time
        if first_ns is None or t < first_ns:
            first_ns = t
        if last_ns is None or t > last_ns:
            last_ns = t
        count += 1
        topics.add(channel.topic)
    return (first_ns or 0, last_ns or 0, count, topics)


def _load_file(
    session: zenoh.Session, args: argparse.Namespace, path: pathlib.Path
) -> None:
    """Open an MCAP file and reset replay state. Acquires STATE_LOCK itself.

    Emits progress samples through STATE.load_progress_pct + an immediate
    replay_status broadcast at key checkpoints so a watching client sees the
    file open, summary read, and publishers declared in sequence rather than
    one all-at-once transition.
    """
    t_start = time.perf_counter()
    logger.info("[LOAD] opening: %s", path)
    with STATE_LOCK:
        _set_state(PubReplayStatus.LOADING, reason="load_file in flight")
        STATE.load_progress_pct = 0.0
        STATE.last_load_error = ""  # clear any prior failure as we try afresh
    COMMAND_EVENT.set()  # break the replay thread out of any active iterator
    _publish_status_now()

    fh = path.open("rb")
    _set_load_progress(10.0)
    try:
        reader = make_reader(fh)
        summary = None
        try:
            summary = reader.get_summary()
        except Exception:
            logger.warning("[LOAD] summary unreadable for %s; stats unavailable", path)
        _set_load_progress(50.0)

        # Recover the time range / count by scanning when the summary lacks
        # statistics, so seek / set_segment / progress work instead of silently
        # degrading (the scrubber UI locks its timeline when start==end==0).
        # Runs here, off STATE_LOCK, so a large scan never blocks RPC/status.
        have_stats = summary is not None and summary.statistics is not None
        scanned: Optional[tuple[int, int, int, set[str]]] = None
        if not have_stats:
            logger.info("[LOAD] no statistics for %s; scanning to recover range", path)
            scanned = _scan_file(reader)
            _set_load_progress(80.0)

        # Loopback guard: refuse to replay channels we'd publish onto our own
        # key. Without --replay-key-tag the recorded topic is the published
        # topic verbatim, so a recorder co-located on the bus would re-capture
        # the daemon's output under the same key. The tag flag side-steps it.
        # Topics come from the summary when present, else from the scan above.
        if not args.replay_key_tag:
            own_prefix = f"/@v0/{args.entity_id}/pubsub/"
            own_suffix = f"/{args.source_id}"
            candidate_topics = (
                [c.topic for c in summary.channels.values()]
                if summary is not None
                else (sorted(scanned[3]) if scanned is not None else [])
            )
            collisions = [
                topic
                for topic in candidate_topics
                if own_prefix in topic and topic.endswith(own_suffix)
            ]
            if collisions:
                raise ValueError(
                    "refusing to load: file contains channels owned by this "
                    f"daemon's own entity-id/source-id (e.g. {collisions[0]!r}). "
                    "Use a different --entity-id/--source-id or pass "
                    "--replay-key-tag to disambiguate."
                )
    except Exception:
        fh.close()
        with STATE_LOCK:
            STATE.load_progress_pct = 0.0
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
            span_s = (
                (stats.message_end_time - stats.message_start_time) / 1e9
                if stats.message_end_time > stats.message_start_time
                else 0.0
            )
            logger.info(
                "[LOAD] summary read: msgs=%d channels=%d span=%.1fs",
                stats.message_count,
                stats.channel_count,
                span_s,
            )
        else:
            first_ns, last_ns, count, topics = scanned
            STATE.start_time_ns = first_ns
            STATE.end_time_ns = last_ns
            STATE.current_time_ns = first_ns
            STATE.total_message_count = count
            STATE.channel_count = len(topics)
            logger.info(
                "[LOAD] scan recovered: msgs=%d channels=%d", count, len(topics)
            )

        if summary is not None:
            _declare_publishers_from_summary(session, summary, args.replay_key_tag)

        _set_state(PubReplayStatus.PAUSED, reason="load_file complete")
        STATE.load_progress_pct = 0.0
    PAUSE_EVENT.clear()
    _publish_status_now()
    logger.info(
        "[LOAD] ready in %.1fms (file=%s msgs=%d)",
        (time.perf_counter() - t_start) * 1000.0,
        path,
        STATE.total_message_count,
    )


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
        logger.debug("[LOAD] lazy publisher: %s", topic)
        pub = session.declare_publisher(topic)
        STATE.publishers[channel.id] = pub
    return pub


def _emit(
    session: zenoh.Session, args: argparse.Namespace, channel: Channel, message: Message
) -> None:
    """Serialize and publish a single MCAP message — unless filtered out."""
    with STATE_LOCK:
        if (
            STATE.channel_filter is not None
            and channel.topic not in STATE.channel_filter
        ):
            return
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
        seg_start = STATE.segment_start_ns
        seg_end = STATE.segment_end_ns

    if reader is None:
        return

    # Effective walk window: explicit seek > segment_start > file start;
    # segment_end (if set) terminates the walk early.
    effective_start = (
        seek_target
        if seek_target is not None
        else (seg_start if seg_start is not None else (start_ns or None))
    )
    # NOTE: MCAP's end_time is exclusive ("logged at or after ... not included"),
    # so omit it — we want every message through EOF. seek sets start_time only.
    iterator = reader.iter_messages(
        log_time_order=True,
        start_time=effective_start,
    )

    first = None
    reference_wall_ns = None
    seek_first_emit = seek_target is not None
    last_speed = None

    for _, channel, message in iterator:
        if shutdown.is_requested() or COMMAND_EVENT.is_set():
            return
        # Segment-end short-circuit. Inclusive: > end means past the segment.
        if seg_end is not None and message.log_time > seg_end:
            return

        # Block while paused. Re-check stop/load/seek on wake. `paused_here`
        # records that we blocked, so the timing anchor is restarted on resume.
        paused_here = False
        while not PAUSE_EVENT.is_set():
            paused_here = True
            if shutdown.is_requested() or COMMAND_EVENT.is_set():
                return
            PAUSE_EVENT.wait(timeout=0.2)

        with STATE_LOCK:
            speed = STATE.playback_speed
            stepping = STATE.step_remaining > 0

        current = message.log_time
        if stepping:
            # Step mode bypasses timing: emit immediately, defer the
            # PAUSED transition until after _emit so the counter and
            # current_time get updated first.
            first = None  # re-anchor for any subsequent play()
            reference_wall_ns = None
            seek_first_emit = False
        elif first is None or seek_first_emit or paused_here or speed != last_speed:
            # (Re)anchor the wall clock here — at start, after a seek, on resume
            # from pause, or when the speed changed. The anchor keeps ticking in
            # real time, so if it isn't reset on resume/speed-change the next
            # message (and everything up to the elapsed gap) is "overdue" and
            # bursts out at once instead of continuing at the recording's
            # cadence. This message becomes the new reference.
            first = current
            reference_wall_ns = time.time_ns()
            seek_first_emit = False
        else:
            lag = int((current - first) / max(speed, 1e-6))
            # Sleep in bounded slices so the CPU idles through inter-message
            # gaps while stop/seek/pause still take effect within ~5 ms. A raw
            # time.sleep(0.0) only yields the GIL — on this long-lived daemon it
            # would spin a core through any quiet stretch of the recording.
            while True:
                remaining_ns = lag - (time.time_ns() - reference_wall_ns)
                if remaining_ns <= 0:
                    break
                if shutdown.is_requested() or COMMAND_EVENT.is_set():
                    return
                if not PAUSE_EVENT.is_set():
                    break
                time.sleep(min(remaining_ns / 1e9, 0.005))

        last_speed = speed
        _emit(session, args, channel, message)

        with STATE_LOCK:
            # Drop the increment if a STOP/LOAD raced with this emit — the
            # handler already reset the counters and the next loop iteration
            # will exit on COMMAND_EVENT anyway.
            if STATE.state == PubReplayStatus.PLAYING:
                STATE.current_time_ns = current
                STATE.played_message_count += 1
                if STATE.step_remaining > 0:
                    STATE.step_remaining -= 1
                    if STATE.step_remaining == 0:
                        _set_state(PubReplayStatus.PAUSED, reason="step done")
                        PAUSE_EVENT.clear()
                        _publish_status_now()
                        return  # let _replay_loop go back to idle


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

        # Either EOF or a step-induced transition to PAUSED. If state is no
        # longer PLAYING (step exhausted its budget), treat it as a normal
        # pause and don't overwrite the state.
        with STATE_LOCK:
            if STATE.state != PubReplayStatus.PLAYING:
                continue
            loop = STATE.loop
            if loop:
                STATE.played_message_count = 0
                # Loop back to segment start if a segment is active; else file start.
                loop_target = (
                    STATE.segment_start_ns
                    if STATE.segment_start_ns is not None
                    else STATE.start_time_ns
                )
                STATE.current_time_ns = loop_target
                STATE.seek_target_ns = loop_target
                logger.info("[REPLAY] EOF; looping")
            else:
                _set_state(PubReplayStatus.STOPPED, reason="EOF")
                STATE.current_time_ns = (
                    STATE.segment_end_ns
                    if STATE.segment_end_ns is not None
                    else STATE.end_time_ns
                )
                # Reconcile counter with total — MCAP summary statistics can
                # under-report (messages outside the indexed range still get
                # iterated), so the raw counter can exceed total at EOF.
                if STATE.total_message_count:
                    STATE.played_message_count = STATE.total_message_count
                logger.info("[REPLAY] EOF; stopping")
                PAUSE_EVENT.set()  # leave the event "run" so next play starts cleanly


# ---------------------------------------------------------------------------
# Status publishing
# ---------------------------------------------------------------------------


def _clamped_played() -> int:
    """played_message_count clamped to total. Defensive — pairs with the EOF
    reconciliation in `_replay_loop` so no >100% value can reach the wire even
    if the counter races past total between iterator increment and EOF."""
    played = STATE.played_message_count
    total = STATE.total_message_count
    return min(played, total) if total else played


def _build_pub_status() -> PubReplayStatus:
    """Snapshot STATE into a ReplayStatus pub payload. Caller holds STATE_LOCK."""
    played = _clamped_played()
    msg = PubReplayStatus(
        state=STATE.state,
        playback_speed=STATE.playback_speed,
        loaded_file=str(STATE.loaded_file) if STATE.loaded_file is not None else "",
        total_message_count=STATE.total_message_count,
        played_message_count=played,
        loop=STATE.loop,
    )
    if STATE.start_time_ns:
        msg.start_time.FromNanoseconds(STATE.start_time_ns)
    if STATE.end_time_ns:
        msg.end_time.FromNanoseconds(STATE.end_time_ns)
    if STATE.current_time_ns:
        msg.current_time.FromNanoseconds(STATE.current_time_ns)
    if STATE.total_message_count:
        msg.progress_pct = min(100.0, 100.0 * played / STATE.total_message_count)
    if STATE.segment_start_ns is not None:
        msg.segment_start.FromNanoseconds(STATE.segment_start_ns)
    if STATE.segment_end_ns is not None:
        msg.segment_end.FromNanoseconds(STATE.segment_end_ns)
    if STATE.channel_filter is not None:
        msg.filtered_channels.extend(sorted(STATE.channel_filter))
    msg.load_progress_pct = STATE.load_progress_pct
    msg.last_load_error = STATE.last_load_error
    _fill_daemon_info(msg)
    return msg


def _build_rpc_status() -> RpcReplayStatus:
    """Snapshot STATE into a ReplayStatus RPC response. Caller holds STATE_LOCK."""
    played = _clamped_played()
    msg = RpcReplayStatus(
        state=STATE.state,
        playback_speed=STATE.playback_speed,
        loaded_file=str(STATE.loaded_file) if STATE.loaded_file is not None else "",
        channel_count=STATE.channel_count,
        total_message_count=STATE.total_message_count,
        played_message_count=played,
        loop=STATE.loop,
    )
    if STATE.start_time_ns:
        msg.start_time.FromNanoseconds(STATE.start_time_ns)
    if STATE.end_time_ns:
        msg.end_time.FromNanoseconds(STATE.end_time_ns)
    if STATE.current_time_ns:
        msg.current_time.FromNanoseconds(STATE.current_time_ns)
    if STATE.total_message_count:
        msg.progress_pct = min(100.0, 100.0 * played / STATE.total_message_count)
    if STATE.segment_start_ns is not None:
        msg.segment_start.FromNanoseconds(STATE.segment_start_ns)
    if STATE.segment_end_ns is not None:
        msg.segment_end.FromNanoseconds(STATE.segment_end_ns)
    if STATE.channel_filter is not None:
        msg.filtered_channels.extend(sorted(STATE.channel_filter))
    msg.load_progress_pct = STATE.load_progress_pct
    msg.last_load_error = STATE.last_load_error
    _fill_daemon_info(msg)
    return msg


def _publish_status_now() -> None:
    """Emit one immediate replay_status sample. Safe to call from any thread."""
    pub = _STATUS_PUBLISHER
    if pub is None:
        return
    with STATE_LOCK:
        payload = _build_pub_status().SerializeToString()
    try:
        pub.put(keelson.enclose(payload=payload))
    except Exception:
        logger.exception("Failed to publish immediate replay_status")


def _status_loop(
    session: zenoh.Session, args: argparse.Namespace, shutdown: GracefulShutdown
) -> None:
    global _STATUS_PUBLISHER
    key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, REPLAY_STATUS_SUBJECT, args.source_id
    )
    pub = session.declare_publisher(key)
    _STATUS_PUBLISHER = pub
    logger.info("Publishing replay status on: %s", key)
    try:
        while not shutdown.is_requested():
            with STATE_LOCK:
                payload = _build_pub_status().SerializeToString()
                playing = STATE.state == PubReplayStatus.PLAYING
            try:
                pub.put(keelson.enclose(payload=payload))
            except Exception:
                logger.exception("Failed to publish replay_status")
            period = STATUS_PERIOD_PLAYING_S if playing else STATUS_PERIOD_IDLE_S
            shutdown.wait(timeout=period)
    finally:
        _STATUS_PUBLISHER = None
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


def _reply_err(query, msg: str, code: int = ErrorResponse.Code.UNSPECIFIED) -> None:
    try:
        query.reply_err(
            ErrorResponse(error_description=msg, code=code).SerializeToString()
        )
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
            return _reply_err(
                op.query, "no file loaded", ErrorResponse.Code.INVALID_STATE
            )
        if STATE.state == PubReplayStatus.STOPPED:
            # Restart from the beginning of the file.
            STATE.played_message_count = 0
            STATE.current_time_ns = STATE.start_time_ns
            STATE.seek_target_ns = None
        _set_state(PubReplayStatus.PLAYING, reason="play")
    # Don't set COMMAND_EVENT here — the replay thread is idle in STOPPED/PAUSED
    # when play is called, so there's no iterator to interrupt. Setting it would
    # race against _walk_iterator's clear() and drop the next emit.
    PAUSE_EVENT.set()
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_pause(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    with STATE_LOCK:
        if STATE.state != PubReplayStatus.PLAYING:
            return _reply_err(
                op.query,
                f"cannot pause in state {PubReplayStatus.State.Name(STATE.state)}",
                ErrorResponse.Code.INVALID_STATE,
            )
        _set_state(PubReplayStatus.PAUSED, reason="pause")
    PAUSE_EVENT.clear()
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_stop(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    with STATE_LOCK:
        _set_state(PubReplayStatus.STOPPED, reason="stop")
        STATE.played_message_count = 0
        STATE.current_time_ns = STATE.start_time_ns
        STATE.seek_target_ns = None
        STATE.step_remaining = 0
    PAUSE_EVENT.set()
    COMMAND_EVENT.set()
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_seek(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    req = SeekRequest()
    req.ParseFromString(op.request_bytes)
    target_ns = req.target.ToNanoseconds()
    with STATE_LOCK:
        if STATE.reader is None:
            return _reply_err(
                op.query, "no file loaded", ErrorResponse.Code.INVALID_STATE
            )
        # Effective seek range is the active segment if set, else the file.
        lo = (
            STATE.segment_start_ns
            if STATE.segment_start_ns is not None
            else STATE.start_time_ns
        )
        hi = (
            STATE.segment_end_ns
            if STATE.segment_end_ns is not None
            else STATE.end_time_ns
        )
        if not (lo <= target_ns <= hi):
            return _reply_err(
                op.query,
                f"seek target {target_ns} out of range [{lo}, {hi}]",
                ErrorResponse.Code.OUT_OF_RANGE,
            )
        STATE.seek_target_ns = target_ns
        STATE.current_time_ns = target_ns
        # Keep played_message_count roughly consistent with the new playhead
        # so the status sample doesn't report a stale (count, position) pair.
        # Approximation: message density is uniform across the file. Off by
        # whatever non-uniformity the recording has, which the scrubber UI
        # is already tolerant to.
        if STATE.end_time_ns > STATE.start_time_ns and STATE.total_message_count:
            frac = (target_ns - STATE.start_time_ns) / (
                STATE.end_time_ns - STATE.start_time_ns
            )
            STATE.played_message_count = int(frac * STATE.total_message_count)
    COMMAND_EVENT.set()
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_set_speed(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = SetSpeedRequest()
    req.ParseFromString(op.request_bytes)
    lo, hi = SPEED_RANGE
    if not (lo <= req.speed <= hi):
        return _reply_err(
            op.query,
            f"speed {req.speed} out of range [{lo}, {hi}]",
            ErrorResponse.Code.OUT_OF_RANGE,
        )
    with STATE_LOCK:
        STATE.playback_speed = req.speed
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_set_loop(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = SetLoopRequest()
    req.ParseFromString(op.request_bytes)
    with STATE_LOCK:
        STATE.loop = req.loop
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_set_channel_filter(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = SetChannelFilterRequest()
    req.ParseFromString(op.request_bytes)
    with STATE_LOCK:
        if STATE.reader is None:
            return _reply_err(
                op.query, "no file loaded", ErrorResponse.Code.INVALID_STATE
            )
        STATE.channel_filter = set(req.channels) if req.channels else None
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_set_segment(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    req = SetSegmentRequest()
    req.ParseFromString(op.request_bytes)
    s_ns = req.start.ToNanoseconds() if req.HasField("start") else 0
    e_ns = req.end.ToNanoseconds() if req.HasField("end") else 0
    with STATE_LOCK:
        if STATE.reader is None:
            return _reply_err(
                op.query, "no file loaded", ErrorResponse.Code.INVALID_STATE
            )
        if s_ns == 0 and e_ns == 0:
            STATE.segment_start_ns = None
            STATE.segment_end_ns = None
        else:
            if not (STATE.start_time_ns <= s_ns < e_ns <= STATE.end_time_ns):
                return _reply_err(
                    op.query,
                    "segment out of range or inverted "
                    f"(start={s_ns}, end={e_ns}, file=[{STATE.start_time_ns}, {STATE.end_time_ns}])",
                    ErrorResponse.Code.OUT_OF_RANGE,
                )
            STATE.segment_start_ns = s_ns
            STATE.segment_end_ns = e_ns
            # If the playhead is outside the new segment, snap it to start.
            if not (s_ns <= STATE.current_time_ns <= e_ns):
                STATE.current_time_ns = s_ns
                STATE.seek_target_ns = s_ns
    COMMAND_EVENT.set()
    _publish_status_now()
    _reply_ok(op.query, op.reply_key)


def _handle_step(session: zenoh.Session, args: argparse.Namespace, op: RpcOp) -> None:
    req = StepRequest()
    req.ParseFromString(op.request_bytes)
    n = req.count or 1
    with STATE_LOCK:
        if STATE.reader is None:
            return _reply_err(
                op.query, "no file loaded", ErrorResponse.Code.INVALID_STATE
            )
        STATE.step_remaining = n
        _set_state(PubReplayStatus.PLAYING, reason=f"step start (count={n})")
    # Same reasoning as _handle_play: replay thread is idle in STOPPED/PAUSED,
    # no in-flight iterator to interrupt. Don't set COMMAND_EVENT.
    PAUSE_EVENT.set()
    _publish_status_now()
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


def _load_file_worker(
    session: zenoh.Session, args: argparse.Namespace, path: pathlib.Path
) -> None:
    """Run a single load on the load worker thread. Serialized by _LOAD_LOCK so
    a second load_file queues rather than races the first."""
    with _LOAD_LOCK:
        try:
            _load_file(session, args, path)
        except Exception as exc:
            logger.exception("[LOAD] worker failed for %s", path)
            with STATE_LOCK:
                _set_state(PubReplayStatus.STOPPED, reason="load_file failed")
                STATE.load_progress_pct = 0.0
                STATE.last_load_error = str(exc)
            _publish_status_now()
            # The originating RPC has already returned OK ("accepted"); the
            # client observes failure through the next replay_status sample
            # (state=STOPPED, last_load_error populated).


def _handle_load_file(
    session: zenoh.Session, args: argparse.Namespace, op: RpcOp
) -> None:
    """Validate the request and dispatch the actual load to a worker thread.

    Returns OK as soon as the request is accepted; clients should watch
    replay_status for the transition LOADING → PAUSED (or STOPPED on error).
    """
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
        return _reply_err(
            op.query,
            f"path escapes base directory: {req.path}",
            ErrorResponse.Code.PERMISSION_DENIED,
        )
    if not path.is_file():
        return _reply_err(
            op.query,
            f"file not found: {req.path}",
            ErrorResponse.Code.NOT_FOUND,
        )
    # Flip to LOADING immediately so concurrent get_status reflects the
    # transition before the worker thread starts touching the file.
    with STATE_LOCK:
        _set_state(PubReplayStatus.LOADING, reason="load_file accepted")
        STATE.load_progress_pct = 0.0
    _publish_status_now()
    threading.Thread(
        target=_load_file_worker,
        args=(session, args, path),
        name=f"mcap-load:{path.name}",
        daemon=True,
    ).start()
    # Accepted — the rest of the lifecycle is visible through replay_status.
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
    "step": _handle_step,
    "set_segment": _handle_set_segment,
    "set_channel_filter": _handle_set_channel_filter,
}


# ---- Request summarizers (used by the dispatch log line) -------------------


def _sum_load(b: bytes) -> str:
    r = LoadFileRequest()
    r.ParseFromString(b)
    return f"path={r.path!r}"


def _sum_list(b: bytes) -> str:
    r = ListFilesRequest()
    r.ParseFromString(b)
    return f"pattern={r.pattern!r}" if r.pattern else ""


def _sum_seek(b: bytes) -> str:
    r = SeekRequest()
    r.ParseFromString(b)
    return f"target_ns={r.target.ToNanoseconds()}"


def _sum_speed(b: bytes) -> str:
    r = SetSpeedRequest()
    r.ParseFromString(b)
    return f"speed={r.speed}"


def _sum_loop(b: bytes) -> str:
    r = SetLoopRequest()
    r.ParseFromString(b)
    return f"loop={r.loop}"


def _sum_step(b: bytes) -> str:
    r = StepRequest()
    r.ParseFromString(b)
    return f"count={r.count or 1}"


def _sum_segment(b: bytes) -> str:
    r = SetSegmentRequest()
    r.ParseFromString(b)
    if not r.HasField("start") and not r.HasField("end"):
        return "clear"
    return f"start_ns={r.start.ToNanoseconds()} end_ns={r.end.ToNanoseconds()}"


def _sum_channel_filter(b: bytes) -> str:
    r = SetChannelFilterRequest()
    r.ParseFromString(b)
    chans = list(r.channels)
    if not chans:
        return "clear"
    head = ",".join(chans[:3])
    if len(chans) <= 3:
        return f"channels=[{head}]"
    return f"channels=[{head},...] ({len(chans)} total)"


_REQUEST_SUMMARIZERS: dict[str, Callable[[bytes], str]] = {
    "load_file": _sum_load,
    "list_files": _sum_list,
    "seek": _sum_seek,
    "set_speed": _sum_speed,
    "set_loop": _sum_loop,
    "step": _sum_step,
    "set_segment": _sum_segment,
    "set_channel_filter": _sum_channel_filter,
    # Empty-arg RPCs (play / pause / stop / get_status) have no entry —
    # _summarize_request returns "" for them.
}


def _summarize_request(procedure: str, request_bytes: bytes) -> str:
    fmt = _REQUEST_SUMMARIZERS.get(procedure)
    if fmt is None:
        return ""
    try:
        return fmt(request_bytes)
    except Exception:
        return "<unparseable>"


# ---- Dispatch wrapper with entry/exit/duration logging --------------------


class _ReplyTracker:
    """Wraps a Zenoh query so the dispatch logger can tell whether the handler
    replied OK or with an error, and what code came back, without each handler
    having to thread the outcome up itself."""

    __slots__ = ("_query", "ok", "err_code", "err_text")

    def __init__(self, query):
        self._query = query
        self.ok = False
        self.err_code: Optional[str] = None
        self.err_text: Optional[str] = None

    def reply(self, key_expr, payload):
        self.ok = True
        return self._query.reply(key_expr, payload)

    def reply_err(self, payload):
        try:
            err = ErrorResponse()
            err.ParseFromString(payload)
            self.err_code = ErrorResponse.Code.Name(err.code)
            self.err_text = err.error_description
        except Exception:
            self.err_code = "?"
            self.err_text = "<undecodable>"
        return self._query.reply_err(payload)

    def __getattr__(self, name):
        return getattr(self._query, name)


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

        summary = _summarize_request(procedure, request_bytes)
        logger.info("[RPC] %s(%s) called", procedure, summary)

        tracker = _ReplyTracker(query)
        op = RpcOp(
            query=tracker,
            procedure=procedure,
            reply_key=reply_key,
            request_bytes=request_bytes,
        )
        t0 = time.perf_counter()
        try:
            handler(session, args, op)
        except Exception:
            logger.exception("[RPC] %s handler raised", procedure)
            _reply_err(tracker, traceback.format_exc(), ErrorResponse.Code.INTERNAL)
        dur_ms = (time.perf_counter() - t0) * 1000.0

        if tracker.ok:
            logger.info("[RPC] %s(%s) -> OK in %.1fms", procedure, summary, dur_ms)
        elif tracker.err_code is not None:
            logger.info(
                "[RPC] %s(%s) -> ERR(%s): %s in %.1fms",
                procedure,
                summary,
                tracker.err_code,
                tracker.err_text,
                dur_ms,
            )
        else:
            # Handler returned without replying — shouldn't happen, flag it.
            logger.warning(
                "[RPC] %s(%s) handler returned without reply in %.1fms",
                procedure,
                summary,
                dur_ms,
            )

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
        logger.debug("[RPC] declared queryable: %s", key)
        queryables.append(q)
    base = keelson.construct_rpc_key(args.realm, args.entity_id, "*", args.source_id)
    logger.info("Declared %d RPC queryables under %s", len(queryables), base)
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
        default=None,
        help=(
            "Directory served by list_files and resolved against load_file paths. "
            "Defaults to the parent of --mcap-file if given, otherwise the current "
            "working directory."
        ),
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

    # Resolve --base-directory default: parent of --mcap-file if given, else cwd.
    if args.base_directory is None:
        if args.mcap_file is not None:
            seed = (
                args.mcap_file
                if args.mcap_file.is_absolute()
                else (pathlib.Path.cwd() / args.mcap_file)
            )
            args.base_directory = seed.resolve().parent
        else:
            args.base_directory = pathlib.Path.cwd()

    _init_daemon_info(args)
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
                _set_state(
                    (
                        PubReplayStatus.PAUSED
                        if args.start_paused
                        else PubReplayStatus.PLAYING
                    ),
                    reason="--mcap-file at startup",
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
