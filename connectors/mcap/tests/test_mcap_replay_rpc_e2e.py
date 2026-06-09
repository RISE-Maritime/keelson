"""End-to-end tests for the McapReplayControl RPC surface on mcap-replay.

Spins up a real Zenoh session in the test process and a real mcap-replay
subprocess, then exercises each RPC and the 1 Hz replay_status broadcast.
"""

import logging
import time
from pathlib import Path
from typing import Callable

import pytest
import zenoh
from mcap.writer import Writer

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.McapReplayControl_pb2 import (
    ListFilesRequest,
    ListFilesResponse,
    LoadFileRequest,
    McapReplaySuccessResponse,
    ReplayStatus as RpcReplayStatus,
    SeekRequest,
    SetChannelFilterRequest,
    SetLoopRequest,
    SetSegmentRequest,
    SetSpeedRequest,
    StepRequest,
)
from keelson.payloads.ReplayStatus_pb2 import ReplayStatus as PubReplayStatus
from keelson.scaffolding import create_zenoh_config


REALM = "test-realm"
ENTITY = "test-replayer"
SOURCE = "replayer1"

_logger = logging.getLogger(__name__)


def _rpc_key(procedure: str) -> str:
    return keelson.construct_rpc_key(REALM, ENTITY, procedure, SOURCE)


def _status_key() -> str:
    return keelson.construct_pubsub_key(REALM, ENTITY, "replay_status", SOURCE)


def _make_fixture_mcap(
    path: Path,
    n_messages: int = 20,
    period_ms: int = 50,
    topic: str | None = None,
) -> int:
    """Write a tiny valid MCAP with `n_messages` on a single channel.

    Returns the message count actually written.
    """
    if topic is None:
        topic = f"{REALM}/@v0/fixture/pubsub/raw/source"
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        schema_id = writer.register_schema(name="test/Bytes", encoding="raw", data=b"")
        channel_id = writer.register_channel(
            schema_id=schema_id,
            topic=topic,
            message_encoding="raw",
        )
        base_ns = 1_700_000_000 * 1_000_000_000  # arbitrary stable epoch
        for i in range(n_messages):
            t = base_ns + i * period_ms * 1_000_000
            writer.add_message(
                channel_id=channel_id,
                log_time=t,
                publish_time=t,
                sequence=i,
                data=keelson.enclose(payload=b"x", enclosed_at=t),
            )
        writer.finish()
    return n_messages


def _make_two_channel_mcap(
    path: Path, n_per_channel: int = 10, period_ms: int = 50
) -> tuple[str, str]:
    """Write an MCAP with two interleaved channels. Returns the two topics."""
    topic_a = f"{REALM}/@v0/fixture/pubsub/channel_a/src"
    topic_b = f"{REALM}/@v0/fixture/pubsub/channel_b/src"
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        sid = writer.register_schema(name="test/Bytes", encoding="raw", data=b"")
        cid_a = writer.register_channel(
            schema_id=sid, topic=topic_a, message_encoding="raw"
        )
        cid_b = writer.register_channel(
            schema_id=sid, topic=topic_b, message_encoding="raw"
        )
        base_ns = 1_700_000_000 * 1_000_000_000
        seq = 0
        for i in range(n_per_channel):
            t_a = base_ns + (2 * i) * period_ms * 1_000_000
            t_b = base_ns + (2 * i + 1) * period_ms * 1_000_000
            writer.add_message(
                channel_id=cid_a,
                log_time=t_a,
                publish_time=t_a,
                sequence=seq,
                data=keelson.enclose(payload=b"a", enclosed_at=t_a),
            )
            seq += 1
            writer.add_message(
                channel_id=cid_b,
                log_time=t_b,
                publish_time=t_b,
                sequence=seq,
                data=keelson.enclose(payload=b"b", enclosed_at=t_b),
            )
            seq += 1
        writer.finish()
    return topic_a, topic_b


def _make_no_summary_mcap(path: Path, n_messages: int = 15, period_ms: int = 50) -> int:
    """Write an MCAP whose summary carries no statistics record.

    ``Writer(use_statistics=False)`` omits the statistics block, so
    ``reader.get_summary().statistics`` is ``None`` — the case the daemon must
    recover by scanning rather than degrading to a zeroed (start/end/count)
    state. Returns the message count written.
    """
    topic = f"{REALM}/@v0/fixture/pubsub/raw/source"
    with path.open("wb") as fh:
        writer = Writer(fh, use_statistics=False)
        writer.start()
        schema_id = writer.register_schema(name="test/Bytes", encoding="raw", data=b"")
        channel_id = writer.register_channel(
            schema_id=schema_id, topic=topic, message_encoding="raw"
        )
        base_ns = 1_700_000_000 * 1_000_000_000
        for i in range(n_messages):
            t = base_ns + i * period_ms * 1_000_000
            writer.add_message(
                channel_id=channel_id,
                log_time=t,
                publish_time=t,
                sequence=i,
                data=keelson.enclose(payload=b"x", enclosed_at=t),
            )
        writer.finish()
    return n_messages


class _StatusCollector:
    def __init__(self) -> None:
        self.messages: list[PubReplayStatus] = []

    def __call__(self, sample: zenoh.Sample) -> None:
        try:
            _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
            msg = PubReplayStatus()
            msg.ParseFromString(payload)
            self.messages.append(msg)
        except Exception:
            _logger.exception("Failed to decode ReplayStatus sample")

    def clear(self) -> None:
        self.messages.clear()

    def wait_for(
        self, predicate: Callable[[PubReplayStatus], bool], timeout: float = 6.0
    ) -> PubReplayStatus | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.messages and predicate(self.messages[-1]):
                return self.messages[-1]
            time.sleep(0.05)
        return self.messages[-1] if self.messages else None


def _call_rpc(
    session: zenoh.Session, procedure: str, payload: bytes = b"", timeout: float = 1.0
):
    """Send an RPC and return (ok_replies, err_payloads).

    A short timeout is fine here because callers either retry through
    ``_wait_for_state`` (probe loop) or set a longer timeout explicitly.
    """
    ok: list[zenoh.Reply] = []
    err: list[bytes] = []

    def _cb(reply: zenoh.Reply) -> None:
        try:
            sample = reply.ok
        except Exception:
            sample = None
        if sample is not None:
            ok.append(reply)
        else:
            try:
                err.append(bytes(reply.err.payload.to_bytes()))
            except Exception:
                err.append(b"")

    session.get(_rpc_key(procedure), _cb, payload=payload)
    deadline = time.time() + timeout
    while time.time() < deadline and not (ok or err):
        time.sleep(0.02)
    return ok, err


def _ok_payload(replies: list[zenoh.Reply]) -> bytes:
    assert replies, "no reply received"
    return bytes(replies[0].ok.payload.to_bytes())


def _err_text(err: list[bytes]) -> str:
    assert err, "no error reply received"
    msg = ErrorResponse()
    msg.ParseFromString(err[0])
    return msg.error_description


def _err_code(err: list[bytes]) -> int:
    assert err, "no error reply received"
    msg = ErrorResponse()
    msg.ParseFromString(err[0])
    return msg.code


def _wait_for_state(
    session: zenoh.Session, state: int, timeout: float = 10.0
) -> RpcReplayStatus:
    """Poll get_status until state matches; assert success."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        ok, err = _call_rpc(session, "get_status", timeout=0.5)
        if ok:
            last = RpcReplayStatus()
            last.ParseFromString(_ok_payload(ok))
            if last.state == state:
                return last
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for state {state}; last observed: {last}")


@pytest.fixture
def replayer_session(zenoh_endpoints):
    conf = create_zenoh_config(
        mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
    )
    session = zenoh.open(conf)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def fixture_dir(temp_dir: Path) -> Path:
    """Directory holding two small MCAP files, ready for list_files."""
    d = temp_dir / "fixtures"
    d.mkdir()
    _make_fixture_mcap(d / "first.mcap", n_messages=20)
    _make_fixture_mcap(d / "second.mcap", n_messages=10)
    return d


def _start_replayer(
    factory,
    fixture_dir: Path,
    zenoh_endpoints,
    *,
    mcap_file: Path | None = None,
    extra: list[str] | None = None,
):
    args = [
        "--realm",
        REALM,
        "--entity-id",
        ENTITY,
        "--source-id",
        SOURCE,
        "--base-directory",
        str(fixture_dir),
        "--mode",
        "peer",
        "--connect",
        zenoh_endpoints["connect"],
    ]
    if mcap_file is not None:
        args += ["--mcap-file", str(mcap_file)]
    if extra:
        args += extra
    proc = factory("mcap", "mcap-replay", args)
    proc.start()
    return proc


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------


@pytest.mark.e2e
def test_get_status_when_no_file_loaded(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        status = _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=6.0)
        assert status.loaded_file == ""
        assert status.total_message_count == 0
        assert status.playback_speed == 1.0
        assert status.loop is False
        # DaemonInfo: discovery clients should be able to label the replayer
        # from the broadcast alone.
        assert status.daemon.version != ""
        assert status.daemon.hostname != ""
        assert str(fixture_dir) in status.daemon.base_directory
    finally:
        proc.stop()


@pytest.mark.e2e
def test_replay_status_broadcasts_at_1hz(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    collector = _StatusCollector()
    sub = replayer_session.declare_subscriber(_status_key(), collector)
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        # Wait for 3 status messages — at ~1 Hz that's ~3s budget; give 6s.
        deadline = time.time() + 6.0
        while time.time() < deadline and len(collector.messages) < 3:
            time.sleep(0.1)
        assert (
            len(collector.messages) >= 3
        ), f"expected ≥3 status envelopes, got {len(collector.messages)}"
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_list_files_returns_fixture_files(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(
            replayer_session, "list_files", ListFilesRequest().SerializeToString()
        )
        assert not err, f"unexpected error: {err}"
        resp = ListFilesResponse()
        resp.ParseFromString(_ok_payload(ok))
        names = sorted(f.path for f in resp.files)
        assert names == ["first.mcap", "second.mcap"], names
        # Summary fields should be populated
        first = next(f for f in resp.files if f.path == "first.mcap")
        assert first.message_count == 20
        assert first.size_bytes > 0
        assert first.channel_count == 1
    finally:
        proc.stop()


@pytest.mark.e2e
def test_load_then_play_advances_played_count(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)

        # Load
        ok, err = _call_rpc(
            replayer_session,
            "load_file",
            LoadFileRequest(path="first.mcap").SerializeToString(),
        )
        assert not err, _err_text(err) if err else ""
        ack = McapReplaySuccessResponse()
        ack.ParseFromString(_ok_payload(ok))

        status = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert status.loaded_file.endswith("first.mcap")
        assert status.total_message_count == 20

        # Play
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""

        # Watch the playhead advance.
        end_state = _wait_for_state(
            replayer_session, PubReplayStatus.STOPPED, timeout=8.0
        )
        assert end_state.played_message_count == 20
        assert end_state.progress_pct == pytest.approx(100.0, abs=0.1)
    finally:
        proc.stop()


@pytest.mark.e2e
def test_pause_freezes_playhead(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",  # auto-starts PLAYING
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PLAYING, timeout=6.0)
        # Let some messages flow
        time.sleep(0.3)
        ok, err = _call_rpc(replayer_session, "pause")
        assert not err, _err_text(err) if err else ""

        s1 = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        time.sleep(1.0)
        s2 = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert s2.played_message_count == s1.played_message_count
    finally:
        proc.stop()


@pytest.mark.e2e
def test_resume_after_pause_does_not_burst(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """Resuming from a pause must continue at the recording's cadence, not dump
    the messages that were "due" during the pause all at once. Guards the
    wall-clock re-anchor on resume in _walk_iterator: the timing anchor keeps
    ticking through a pause, so without the reset the remaining messages burst
    out the instant playback resumes."""
    arrivals: list[float] = []
    data_key = f"{REALM}/@v0/fixture/pubsub/raw/source"  # first.mcap's topic
    sub = replayer_session.declare_subscriber(
        data_key, lambda _s: arrivals.append(time.time())
    )
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        # Play briefly, then pause early so most of the ~0.95 s file is ahead.
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        time.sleep(0.15)
        ok, err = _call_rpc(replayer_session, "pause")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED)

        # Hold the pause, then resume and run to EOF.
        time.sleep(2.0)
        n_before = len(arrivals)
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)

        # The post-resume messages must arrive spread over wall-clock time at
        # the recording's ~50 ms cadence — a burst would deliver them within a
        # few milliseconds of each other.
        post = arrivals[n_before:]
        assert len(post) >= 5, f"expected several post-resume messages, got {len(post)}"
        spread = post[-1] - post[0]
        assert spread >= 0.3, f"resume bursted {len(post)} msgs in {spread:.3f}s"
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_stop_resets_playhead(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PLAYING, timeout=6.0)
        time.sleep(0.3)
        ok, err = _call_rpc(replayer_session, "stop")
        assert not err, _err_text(err) if err else ""
        s = _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        assert s.played_message_count == 0
    finally:
        proc.stop()


@pytest.mark.e2e
def test_seek_to_midfile(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        start_ns = s.start_time.ToNanoseconds()
        end_ns = s.end_time.ToNanoseconds()
        mid_ns = start_ns + (end_ns - start_ns) // 2

        req = SeekRequest()
        req.target.FromNanoseconds(mid_ns)
        ok, err = _call_rpc(replayer_session, "seek", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        # Confirm playhead jumped
        deadline = time.time() + 3.0
        while time.time() < deadline:
            ok, _ = _call_rpc(replayer_session, "get_status")
            cur = RpcReplayStatus()
            cur.ParseFromString(_ok_payload(ok))
            if cur.current_time.ToNanoseconds() == mid_ns:
                break
            time.sleep(0.1)
        else:
            pytest.fail("seek did not update current_time")
    finally:
        proc.stop()


@pytest.mark.e2e
def test_seek_out_of_range_errors(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        req = SeekRequest()
        req.target.FromNanoseconds(1)  # far before start
        ok, err = _call_rpc(replayer_session, "seek", req.SerializeToString())
        assert err, f"expected error reply, got ok={ok}"
        assert "out of range" in _err_text(err)
        assert _err_code(err) == ErrorResponse.Code.OUT_OF_RANGE
    finally:
        proc.stop()


@pytest.mark.e2e
def test_set_speed_within_and_outside_range(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        # In-range
        ok, err = _call_rpc(
            replayer_session,
            "set_speed",
            SetSpeedRequest(speed=2.0).SerializeToString(),
        )
        assert not err, _err_text(err) if err else ""
        # Out-of-range
        ok, err = _call_rpc(
            replayer_session,
            "set_speed",
            SetSpeedRequest(speed=10.0).SerializeToString(),
        )
        assert err, "expected error reply for speed=10.0"
        assert "out of range" in _err_text(err)
        assert _err_code(err) == ErrorResponse.Code.OUT_OF_RANGE
    finally:
        proc.stop()


@pytest.mark.e2e
def test_set_loop_toggles(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(
            replayer_session, "set_loop", SetLoopRequest(loop=True).SerializeToString()
        )
        assert not err
        ok, _ = _call_rpc(replayer_session, "get_status")
        cur = RpcReplayStatus()
        cur.ParseFromString(_ok_payload(ok))
        assert cur.loop is True
    finally:
        proc.stop()


@pytest.mark.e2e
def test_loop_replays_from_start_on_eof(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """With loop enabled, reaching EOF re-seeks to the start and keeps PLAYING
    instead of stopping. Watch the broadcast for the climb-then-reset signature:
    under pure play+loop the played counter only ever decreases on a re-seek."""
    collector = _StatusCollector()
    sub = replayer_session.declare_subscriber(_status_key(), collector)
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        total = s.total_message_count
        assert total == 20

        ok, err = _call_rpc(
            replayer_session, "set_loop", SetLoopRequest(loop=True).SerializeToString()
        )
        assert not err, _err_text(err) if err else ""

        collector.clear()
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""

        # first.mcap spans ~0.95 s; collect a few passes so EOF is crossed.
        time.sleep(4.0)

        played_seq = [m.played_message_count for m in collector.messages]
        states = {m.state for m in collector.messages}
        assert played_seq, "no status broadcast captured during loop playback"
        # Reached well into the file — real playback, not just a toggle.
        assert max(played_seq) >= total // 2, played_seq
        # Under pure play+loop the only thing that drops the counter is an EOF
        # re-seek (played reset to 0), so a decrease proves at least one loop.
        looped = any(b < a for a, b in zip(played_seq, played_seq[1:]))
        assert looped, f"counter never reset — loop did not re-seek: {played_seq}"
        # Looping never lands in STOPPED at EOF.
        assert PubReplayStatus.STOPPED not in states, states
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_load_no_summary_statistics_recovers_by_scan(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """A file whose summary lacks statistics must still load with a usable
    time range/count (recovered by scanning) so seek works, instead of
    silently degrading to a [0, 0] range that rejects every seek OUT_OF_RANGE."""
    d = temp_dir / "nostats"
    d.mkdir()
    n = _make_no_summary_mcap(d / "nostats.mcap", n_messages=15)
    proc = _start_replayer(
        connector_process_factory,
        d,
        zenoh_endpoints,
        mcap_file=d / "nostats.mcap",
        extra=["--start-paused"],
    )
    try:
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        # Scan recovered the range + count instead of zeroing them.
        assert s.total_message_count == n
        start_ns = s.start_time.ToNanoseconds()
        end_ns = s.end_time.ToNanoseconds()
        assert end_ns > start_ns > 0, (start_ns, end_ns)

        # A seek into the recovered range now succeeds (was OUT_OF_RANGE).
        mid_ns = start_ns + (end_ns - start_ns) // 2
        req = SeekRequest()
        req.target.FromNanoseconds(mid_ns)
        ok, err = _call_rpc(replayer_session, "seek", req.SerializeToString())
        assert not err, _err_text(err) if err else ""
    finally:
        proc.stop()


@pytest.mark.e2e
def test_play_without_loaded_file_errors(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(replayer_session, "play")
        assert err, "expected error reply with no file loaded"
        assert "no file loaded" in _err_text(err)
        assert _err_code(err) == ErrorResponse.Code.INVALID_STATE
    finally:
        proc.stop()


@pytest.mark.e2e
def test_load_file_rejects_path_traversal(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(
            replayer_session,
            "load_file",
            LoadFileRequest(path="../../etc/hostname").SerializeToString(),
        )
        assert err, "expected error reply for path-escape attempt"
        assert "escapes base directory" in _err_text(err)
        assert _err_code(err) == ErrorResponse.Code.PERMISSION_DENIED
    finally:
        proc.stop()


@pytest.mark.e2e
def test_step_advances_then_pauses(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """step(n) emits n messages and pauses; repeated steps accumulate."""
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        # step(5) → 5 messages, back to PAUSED
        ok, err = _call_rpc(
            replayer_session, "step", StepRequest(count=5).SerializeToString()
        )
        assert not err, _err_text(err) if err else ""
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=5.0)
        assert s.played_message_count == 5
        # step(3) → cumulative 8
        ok, err = _call_rpc(
            replayer_session, "step", StepRequest(count=3).SerializeToString()
        )
        assert not err
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=5.0)
        assert s.played_message_count == 8
        # step() with no count defaults to 1
        ok, err = _call_rpc(replayer_session, "step")
        assert not err
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=5.0)
        assert s.played_message_count == 9
    finally:
        proc.stop()


@pytest.mark.e2e
def test_step_without_loaded_file_errors(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(
            replayer_session, "step", StepRequest(count=1).SerializeToString()
        )
        assert err
        assert _err_code(err) == ErrorResponse.Code.INVALID_STATE
    finally:
        proc.stop()


@pytest.mark.e2e
def test_set_segment_restricts_playback_window(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """Active segment shortens the walk: play covers only segment[start, end]."""
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        s0 = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        start_ns = s0.start_time.ToNanoseconds()
        end_ns = s0.end_time.ToNanoseconds()
        span = end_ns - start_ns
        seg_start = start_ns + span // 4
        seg_end = start_ns + (3 * span) // 4

        req = SetSegmentRequest()
        req.start.FromNanoseconds(seg_start)
        req.end.FromNanoseconds(seg_end)
        ok, err = _call_rpc(replayer_session, "set_segment", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        # Status reflects the segment.
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert s.segment_start.ToNanoseconds() == seg_start
        assert s.segment_end.ToNanoseconds() == seg_end

        # Play to end of segment — should finish at segment_end, state STOPPED.
        ok, err = _call_rpc(replayer_session, "play")
        assert not err
        s_end = _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=5.0)
        # Current time should be at-or-before seg_end (final emitted message).
        assert s_end.current_time.ToNanoseconds() <= seg_end + 1
        assert s_end.current_time.ToNanoseconds() >= seg_start
    finally:
        proc.stop()


@pytest.mark.e2e
def test_set_segment_rejects_inverted_range(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        # Build a clearly-inverted segment (end < start).
        req = SetSegmentRequest()
        req.start.FromNanoseconds(s.end_time.ToNanoseconds())
        req.end.FromNanoseconds(s.start_time.ToNanoseconds())
        ok, err = _call_rpc(replayer_session, "set_segment", req.SerializeToString())
        assert err
        assert _err_code(err) == ErrorResponse.Code.OUT_OF_RANGE
    finally:
        proc.stop()


@pytest.mark.e2e
def test_seek_outside_segment_errors(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        s0 = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        start_ns = s0.start_time.ToNanoseconds()
        end_ns = s0.end_time.ToNanoseconds()
        span = end_ns - start_ns

        # Set a 50% segment and seek to a point in the first quarter (outside).
        seg = SetSegmentRequest()
        seg.start.FromNanoseconds(start_ns + span // 4)
        seg.end.FromNanoseconds(start_ns + (3 * span) // 4)
        ok, err = _call_rpc(replayer_session, "set_segment", seg.SerializeToString())
        assert not err

        outside = SeekRequest()
        outside.target.FromNanoseconds(start_ns + 10)  # before segment_start
        ok, err = _call_rpc(replayer_session, "seek", outside.SerializeToString())
        assert err
        assert _err_code(err) == ErrorResponse.Code.OUT_OF_RANGE
    finally:
        proc.stop()


@pytest.mark.e2e
def test_channel_filter_mutes_other_channels(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """set_channel_filter allowlist suppresses non-listed channel emissions."""
    d = temp_dir / "two_chan"
    d.mkdir()
    topic_a, topic_b = _make_two_channel_mcap(d / "two.mcap", n_per_channel=8)

    received_a: list[bytes] = []
    received_b: list[bytes] = []
    sub_a = replayer_session.declare_subscriber(
        topic_a, lambda s: received_a.append(bytes(s.payload.to_bytes()))
    )
    sub_b = replayer_session.declare_subscriber(
        topic_b, lambda s: received_b.append(bytes(s.payload.to_bytes()))
    )

    proc = _start_replayer(
        connector_process_factory,
        d,
        zenoh_endpoints,
        mcap_file=d / "two.mcap",
        extra=["--start-paused"],
    )
    try:
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        assert s.total_message_count == 16

        # Filter: only channel A.
        ok, err = _call_rpc(
            replayer_session,
            "set_channel_filter",
            SetChannelFilterRequest(channels=[topic_a]).SerializeToString(),
        )
        assert not err, _err_text(err) if err else ""

        # Confirm status reflects the filter.
        s2 = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert list(s2.filtered_channels) == [topic_a]

        # Play to end.
        ok, err = _call_rpc(replayer_session, "play")
        assert not err
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=5.0)
        # Give Zenoh a moment to deliver in-flight samples.
        time.sleep(0.5)

        assert len(received_a) == 8, f"expected 8 on A, got {len(received_a)}"
        assert len(received_b) == 0, f"expected 0 on B, got {len(received_b)}"
    finally:
        sub_a.undeclare()
        sub_b.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_channel_filter_clears_with_empty_list(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """Empty set_channel_filter clears the filter; status reflects it."""
    d = temp_dir / "two_chan_clear"
    d.mkdir()
    topic_a, _ = _make_two_channel_mcap(d / "two.mcap", n_per_channel=4)

    proc = _start_replayer(
        connector_process_factory,
        d,
        zenoh_endpoints,
        mcap_file=d / "two.mcap",
        extra=["--start-paused"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        ok, err = _call_rpc(
            replayer_session,
            "set_channel_filter",
            SetChannelFilterRequest(channels=[topic_a]).SerializeToString(),
        )
        assert not err
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert list(s.filtered_channels) == [topic_a]

        ok, err = _call_rpc(
            replayer_session,
            "set_channel_filter",
            SetChannelFilterRequest(channels=[]).SerializeToString(),
        )
        assert not err
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert list(s.filtered_channels) == []
    finally:
        proc.stop()


@pytest.mark.e2e
def test_load_file_returns_immediately_and_transitions_through_loading(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """load_file returns OK fast and the LOADING state is observable on the
    broadcast before settling at PAUSED."""
    collector = _StatusCollector()
    sub = replayer_session.declare_subscriber(_status_key(), collector)
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=6.0)
        collector.clear()

        t0 = time.time()
        ok, err = _call_rpc(
            replayer_session,
            "load_file",
            LoadFileRequest(path="first.mcap").SerializeToString(),
            timeout=2.0,
        )
        elapsed = time.time() - t0
        assert not err, _err_text(err) if err else ""
        # Acceptance reply should come back fast — the load itself runs on a
        # worker thread, not in the RPC callback.
        assert elapsed < 1.0, f"load_file RPC took {elapsed:.2f}s (expected <1s)"

        # Wait for PAUSED (final state after load completes) on the broadcast.
        final = collector.wait_for(
            lambda s: s.state == PubReplayStatus.PAUSED, timeout=6.0
        )
        assert final is not None, "never saw PAUSED on the broadcast"
        assert final.loaded_file.endswith("first.mcap")

        # And we should have seen at least one LOADING sample in between.
        states_seen = [s.state for s in collector.messages]
        assert (
            PubReplayStatus.LOADING in states_seen
        ), f"never saw LOADING in broadcast; states={states_seen}"
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_loopback_guard_rejects_self_publishing_file(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """A file whose channel topic matches the daemon's own key is refused.

    Since load_file is now async, the loopback collision is surfaced via the
    replay_status broadcast's last_load_error field rather than the RPC reply.
    """
    d = temp_dir / "loop_fixtures"
    d.mkdir()
    # Channel topic matches the daemon's published key for this entity/source.
    own_topic = f"{REALM}/@v0/{ENTITY}/pubsub/raw/{SOURCE}"
    _make_fixture_mcap(d / "self.mcap", n_messages=5, topic=own_topic)

    collector = _StatusCollector()
    sub = replayer_session.declare_subscriber(_status_key(), collector)
    proc = _start_replayer(connector_process_factory, d, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        collector.clear()
        ok, err = _call_rpc(
            replayer_session,
            "load_file",
            LoadFileRequest(path="self.mcap").SerializeToString(),
        )
        # RPC itself accepts (path is valid + file exists) — the loopback
        # check happens once the worker opens the file and reads the summary.
        assert not err, f"unexpected sync error: {_err_text(err) if err else ''}"

        # The async failure should land as STOPPED + non-empty last_load_error.
        bad = collector.wait_for(
            lambda s: s.state == PubReplayStatus.STOPPED and s.last_load_error,
            timeout=6.0,
        )
        assert bad is not None, "never saw STOPPED + last_load_error on broadcast"
        assert "--replay-key-tag" in bad.last_load_error
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_loopback_guard_passes_with_replay_key_tag(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """Same file as above loads cleanly when --replay-key-tag is set."""
    d = temp_dir / "loop_fixtures_tagged"
    d.mkdir()
    own_topic = f"{REALM}/@v0/{ENTITY}/pubsub/raw/{SOURCE}"
    _make_fixture_mcap(d / "self.mcap", n_messages=5, topic=own_topic)
    proc = _start_replayer(
        connector_process_factory,
        d,
        zenoh_endpoints,
        extra=["--replay-key-tag"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(
            replayer_session,
            "load_file",
            LoadFileRequest(path="self.mcap").SerializeToString(),
        )
        assert not err, f"unexpected error: {_err_text(err) if err else ''}"
    finally:
        proc.stop()


# =============================================================================
# Log-trace tests — assert the operator-visible audit log lines exist.
# =============================================================================


@pytest.mark.e2e
def test_log_contains_rpc_and_state_traces(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """A scripted play/pause sequence should leave [RPC], [STATE], and [LOAD]
    lines in the daemon's stderr."""
    proc = _start_replayer(
        connector_process_factory,
        fixture_dir,
        zenoh_endpoints,
        mcap_file=fixture_dir / "first.mcap",
        extra=["--start-paused"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        _call_rpc(replayer_session, "play")
        time.sleep(0.3)
        _call_rpc(replayer_session, "pause")
        time.sleep(0.3)
    finally:
        proc.stop()
    _stdout, stderr = proc.logs()

    # RPC entry + exit lines for both calls
    assert "[RPC] play() called" in stderr
    assert "[RPC] play() -> OK" in stderr
    assert "[RPC] pause() called" in stderr
    assert "[RPC] pause() -> OK" in stderr
    # State transition lines for the same calls
    assert "[STATE] PAUSED -> PLAYING (reason=play)" in stderr
    assert "[STATE] PLAYING -> PAUSED (reason=pause)" in stderr
    # Load lifecycle
    assert "[LOAD] opening:" in stderr
    assert "[LOAD] ready in" in stderr


@pytest.mark.e2e
def test_log_traces_error_response(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """An error RPC leaves a [RPC] ... -> ERR(...) line carrying the typed code."""
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=6.0)
        _call_rpc(replayer_session, "play")  # no file loaded
        time.sleep(0.2)
    finally:
        proc.stop()
    _stdout, stderr = proc.logs()
    assert "[RPC] play() -> ERR(INVALID_STATE): no file loaded" in stderr
