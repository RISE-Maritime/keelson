"""End-to-end tests for the ReplayControl RPC surface on mcap-replay.

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
from keelson.interfaces.ReplayControl_pb2 import (
    DescribeFileRequest,
    DescribeFileResponse,
    ListFilesRequest,
    ListFilesResponse,
    LoadFileRequest,
    ReplaySuccessResponse,
    SeekRequest,
    SetLoopRequest,
    SetRangeRequest,
    SetSpeedRequest,
)
from keelson.payloads.ReplayStatus_pb2 import ReplayStatus as PubReplayStatus
from keelson.scaffolding import create_zenoh_config


REALM = "test-realm"
ENTITY = "test-replayer"
SOURCE = "replayer1"

# How many messages `first.mcap` holds, at the 50 ms default cadence -- so the
# file is ~0.95 s long. Named because two tests count delivered messages against
# it, and a silent change to the fixture would turn those assertions into noise.
_FIXTURE_MESSAGES = 20

_logger = logging.getLogger(__name__)


def _rpc_key(procedure: str) -> str:
    return keelson.construct_rpc_key(
        REALM, ENTITY, "replay_control", "v1", procedure, SOURCE
    )


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


def _make_identified_mcap(
    path: Path, topics: list[str], n_per_topic: int = 8, period_ms: int = 50
) -> dict[str, list[bytes]]:
    """Write a multi-channel MCAP where every message carries a unique,
    recognizable payload (``f"{topic}#{i}"``) so a replay subscriber can verify
    exact payload fidelity, per-channel completeness, and ordering.

    Messages are interleaved across topics in log-time order. Returns
    ``{topic: [payload_bytes, ... in emit order]}``.
    """
    expected: dict[str, list[bytes]] = {t: [] for t in topics}
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        sid = writer.register_schema(name="test/Bytes", encoding="raw", data=b"")
        cids = {
            t: writer.register_channel(schema_id=sid, topic=t, message_encoding="raw")
            for t in topics
        }
        base_ns = 1_700_000_000 * 1_000_000_000
        seq = 0
        slot = 0
        for i in range(n_per_topic):
            for t in topics:
                payload = f"{t}#{i}".encode()
                ts = base_ns + slot * period_ms * 1_000_000
                writer.add_message(
                    channel_id=cids[t],
                    log_time=ts,
                    publish_time=ts,
                    sequence=seq,
                    data=payload,
                )
                expected[t].append(payload)
                seq += 1
                slot += 1
        writer.finish()
    return expected


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


def _latest_status(
    session: zenoh.Session,
    predicate: Callable[[PubReplayStatus], bool] | None = None,
    timeout: float = 6.0,
) -> PubReplayStatus | None:
    """Subscribe to the replay_status broadcast and return the latest sample
    matching ``predicate`` (or just the latest), or ``None`` on timeout.

    State is observed through the broadcast rather than an RPC — there is no
    get_status procedure; the daemon publishes continuously (1 Hz idle, 5 Hz
    playing) and an immediate sample on every state mutation.
    """
    collector = _StatusCollector()
    sub = session.declare_subscriber(_status_key(), collector)
    try:
        return collector.wait_for(predicate or (lambda _s: True), timeout=timeout)
    finally:
        sub.undeclare()


def _wait_for_state(
    session: zenoh.Session, state: int, timeout: float = 10.0
) -> PubReplayStatus:
    """Wait until the replay_status broadcast reports ``state``; assert success."""
    last = _latest_status(session, lambda s: s.state == state, timeout=timeout)
    if last is None or last.state != state:
        raise AssertionError(
            f"timed out waiting for state {state}; last observed: {last}"
        )
    return last


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
    _make_fixture_mcap(d / "first.mcap", n_messages=_FIXTURE_MESSAGES)
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
def test_initial_state_when_no_file_loaded(
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
        ack = ReplaySuccessResponse()
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
def test_replay_delivers_payloads_intact_on_original_keys(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """The connector's core data-plane contract: every recorded message is
    republished on its original Zenoh key, payload intact, in order, and all of
    them arrive. Exercises multi-channel replay (per-channel publisher
    declaration) end-to-end, which the RPC/state tests don't touch."""
    d = temp_dir / "fidelity"
    d.mkdir()
    topic_a = f"{REALM}/@v0/fixture/pubsub/channel_a/src"
    topic_b = f"{REALM}/@v0/fixture/pubsub/channel_b/src"
    expected = _make_identified_mcap(d / "two.mcap", [topic_a, topic_b], n_per_topic=8)

    received: dict[str, list[bytes]] = {topic_a: [], topic_b: []}

    def _collector(topic: str):
        def _cb(sample: zenoh.Sample) -> None:
            _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
            received[topic].append(payload)

        return _cb

    sub_a = replayer_session.declare_subscriber(topic_a, _collector(topic_a))
    sub_b = replayer_session.declare_subscriber(topic_b, _collector(topic_b))

    # --start-paused so the subscribers are matched before any message flows;
    # then play to EOF.
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
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)
        # Let any in-flight samples drain before asserting.
        time.sleep(0.5)

        # Completeness + fidelity + per-channel ordering. A single uncover()
        # yields back exactly the recorded message bytes, since _emit re-encloses
        # message.data into one envelope.
        assert received[topic_a] == expected[topic_a], (
            len(received[topic_a]),
            len(expected[topic_a]),
        )
        assert received[topic_b] == expected[topic_b], (
            len(received[topic_b]),
            len(expected[topic_b]),
        )
    finally:
        sub_a.undeclare()
        sub_b.undeclare()
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

        # Confirm playhead jumped — observed through the status broadcast.
        cur = _latest_status(
            replayer_session,
            lambda s: s.current_time.ToNanoseconds() == mid_ns,
            timeout=3.0,
        )
        if cur is None or cur.current_time.ToNanoseconds() != mid_ns:
            pytest.fail("seek did not update current_time")
    finally:
        proc.stop()


@pytest.mark.e2e
def test_seek_while_stopped_survives_play(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """A seek accepted while STOPPED must still be honoured by the next play.

    _handle_play used to clear seek_target_ns unconditionally when resuming from
    STOPPED, so "stop, seek, play" started at 0:00 while every observable said
    otherwise: the seek RPC replied ok and the status broadcast showed the
    requested position. A client could only work around it by playing first and
    seeking second, which the operator sees as a jump that also starts playback.

    Asserted on the DELIVERED MESSAGES, not on the status broadcast. first.mcap
    is ~0.95 s long, so PLAYING is gone before a subscriber declared after the
    play RPC can see it -- the same reason test_resume_after_pause_does_not_burst
    watches the data key. Playing from the midpoint delivers about half the file;
    the discarded-seek bug delivers all of it.
    """
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
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        start_ns = s.start_time.ToNanoseconds()
        end_ns = s.end_time.ToNanoseconds()
        mid_ns = start_ns + (end_ns - start_ns) // 2

        ok, err = _call_rpc(replayer_session, "stop")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)

        req = SeekRequest()
        req.target.FromNanoseconds(mid_ns)
        ok, err = _call_rpc(replayer_session, "seek", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        n_before = len(arrivals)
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)

        # 20 messages at 50 ms; from the midpoint about 10 remain. The bug
        # replays the whole file, so anything at or near 20 is the regression.
        delivered = len(arrivals) - n_before
        assert 0 < delivered <= 14, (
            f"expected roughly half of {_FIXTURE_MESSAGES} messages from the "
            f"midpoint, got {delivered} -- play discarded the pending seek"
        )
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_stop_then_play_without_a_seek_still_restarts(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """The other half of the contract: with nothing pending, play still rewinds.

    The fix above is conditional on seek_target_ns, so this pins that an
    ordinary stop-then-play is unchanged -- otherwise a stopped replay would
    resume where it left off, which is what pause is for. Stopping about two
    thirds through makes the two outcomes far apart: a rewind delivers the whole
    file again, a resume delivers only the remainder.
    """
    arrivals: list[float] = []
    data_key = f"{REALM}/@v0/fixture/pubsub/raw/source"
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
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        time.sleep(0.6)  # ~12 of 20 messages, well short of the ~0.95 s EOF
        ok, err = _call_rpc(replayer_session, "stop")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)

        n_before = len(arrivals)
        assert n_before > 0, "nothing played before the stop; test cannot discriminate"

        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)

        delivered = len(arrivals) - n_before
        assert delivered >= _FIXTURE_MESSAGES - 4, (
            f"expected the whole file again after stop-then-play, got "
            f"{delivered} of {_FIXTURE_MESSAGES} -- play resumed instead of rewinding"
        )
    finally:
        sub.undeclare()
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
        # The fast end of the range. 10x was the out-of-range example while
        # the ceiling was 4x; it must now be accepted, and so must the bound.
        for fast in (10.0, 20.0):
            ok, err = _call_rpc(
                replayer_session,
                "set_speed",
                SetSpeedRequest(speed=fast).SerializeToString(),
            )
            assert not err, f"speed={fast}: " + (_err_text(err) if err else "")
        # Out-of-range, above and below
        for bad in (25.0, 0.1):
            ok, err = _call_rpc(
                replayer_session,
                "set_speed",
                SetSpeedRequest(speed=bad).SerializeToString(),
            )
            assert err, f"expected error reply for speed={bad}"
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
        cur = _latest_status(replayer_session, lambda s: s.loop is True, timeout=3.0)
        assert cur is not None and cur.loop is True
    finally:
        proc.stop()


@pytest.mark.e2e
def test_range_bounds_playback(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """A range bounds what is replayed, at BOTH ends.

    Asserted on delivered messages, not on the status broadcast: first.mcap is ~0.95 s long, so a
    bound that only appeared in status could be satisfied by a daemon that published the whole file
    anyway. The middle third is about 7 of 20 messages; publishing all 20 is the regression.
    """
    arrivals: list[float] = []
    data_key = f"{REALM}/@v0/fixture/pubsub/raw/source"
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
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        start_ns = s.start_time.ToNanoseconds()
        end_ns = s.end_time.ToNanoseconds()
        span = end_ns - start_ns

        req = SetRangeRequest()
        req.start.FromNanoseconds(start_ns + span // 3)
        req.end.FromNanoseconds(start_ns + (2 * span) // 3)
        ok, err = _call_rpc(replayer_session, "set_range", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        # Reported back, so a station that did not set it can still show it.
        st = _latest_status(
            replayer_session, lambda x: x.HasField("range_end"), timeout=3.0
        )
        assert st is not None, "the active range is not reported in replay_status"
        assert st.range_start.ToNanoseconds() == start_ns + span // 3

        n_before = len(arrivals)
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)

        delivered = len(arrivals) - n_before
        assert 0 < delivered < _FIXTURE_MESSAGES, (
            f"expected only the middle of {_FIXTURE_MESSAGES} messages, got {delivered} "
            "-- the range did not bound playback"
        )
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_range_loop_rewinds_to_the_range_start(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """With a range AND loop, the rewind goes to the RANGE start, not the file start.

    This is the assertion the feature exists for. Rewinding to the file start would replay material
    the operator deliberately excluded and then run into the range end again, so every cycle would
    begin in the wrong place -- while still looking like a working loop.
    """
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
        start_ns = s.start_time.ToNanoseconds()
        end_ns = s.end_time.ToNanoseconds()
        range_start = start_ns + (end_ns - start_ns) // 2

        req = SetRangeRequest()
        req.start.FromNanoseconds(range_start)
        ok, err = _call_rpc(replayer_session, "set_range", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        lreq = SetLoopRequest()
        lreq.loop = True
        ok, err = _call_rpc(replayer_session, "set_loop", lreq.SerializeToString())
        assert not err, _err_text(err) if err else ""

        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""

        # Two full passes of the half-file at 1x is ~1 s; allow generously for CI.
        deadline = time.time() + 8.0
        while time.time() < deadline:
            time.sleep(0.2)
        times = [
            x.current_time.ToNanoseconds()
            for x in collector.messages
            if x.state == PubReplayStatus.PLAYING and x.HasField("current_time")
        ]
        assert times, "no PLAYING samples observed"
        # Nothing may ever be published from before the range start -- that is the whole claim.
        assert min(times) >= range_start, (
            f"playhead reached {min(times)}, before the range start {range_start} "
            "-- the loop rewound to the file start"
        )
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_range_validation_and_clearing(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """An inverted or out-of-window range is refused; both fields unset clears it.

    Inverted is REFUSED rather than silently swapped: a client that read its range back and found
    the bounds exchanged, with nothing saying so, would have no way to know its own gesture had
    been reinterpreted.
    """
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

        bad = SetRangeRequest()
        bad.start.FromNanoseconds(end_ns)
        bad.end.FromNanoseconds(start_ns)
        ok, err = _call_rpc(replayer_session, "set_range", bad.SerializeToString())
        assert err, f"expected an error reply, got ok={ok}"
        assert _err_code(err) == ErrorResponse.Code.OUT_OF_RANGE

        far = SetRangeRequest()
        far.start.FromNanoseconds(1)
        ok, err = _call_rpc(replayer_session, "set_range", far.SerializeToString())
        assert err, f"expected an error reply, got ok={ok}"
        assert "out of range" in _err_text(err)

        good = SetRangeRequest()
        good.start.FromNanoseconds(start_ns)
        good.end.FromNanoseconds(end_ns)
        ok, err = _call_rpc(replayer_session, "set_range", good.SerializeToString())
        assert not err, _err_text(err) if err else ""
        assert _latest_status(
            replayer_session, lambda x: x.HasField("range_end"), timeout=3.0
        )

        # Both unset clears it -- which works because they are message fields, and proto3
        # gives a message field explicit presence without an `optional` keyword.
        clear = SetRangeRequest()
        ok, err = _call_rpc(replayer_session, "set_range", clear.SerializeToString())
        assert not err, _err_text(err) if err else ""
        cleared = _latest_status(
            replayer_session, lambda x: not x.HasField("range_end"), timeout=3.0
        )
        assert cleared is not None, "an unset range was not cleared"
        assert not cleared.HasField("range_start")
    finally:
        proc.stop()


@pytest.mark.e2e
def test_seek_outside_the_active_range_errors(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """With a range set, a seek outside it is refused -- at both ends.

    Accepted, a seek before the range start plays from there to the range end, so "nothing is
    published from before the start" -- the claim the loop test rests on -- would hold only until
    somebody dragged the scrubber. A seek past the range end walks an empty iterator and stops or
    snaps back at once, which reads as a hang rather than as a refusal.
    """
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
        span = end_ns - start_ns
        range_start = start_ns + span // 3
        range_end = start_ns + (2 * span) // 3

        req = SetRangeRequest()
        req.start.FromNanoseconds(range_start)
        req.end.FromNanoseconds(range_end)
        ok, err = _call_rpc(replayer_session, "set_range", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        # Before the in-point and after the out-point: both inside the FILE, both refused.
        for label, target in (
            ("before the range start", range_start - span // 10),
            ("after the range end", range_end + span // 10),
        ):
            bad = SeekRequest()
            bad.target.FromNanoseconds(target)
            ok, err = _call_rpc(replayer_session, "seek", bad.SerializeToString())
            assert err, f"seek {label} was accepted (ok={ok})"
            assert _err_code(err) == ErrorResponse.Code.OUT_OF_RANGE
            assert "active range" in _err_text(err), _err_text(err)

        # Inside it still works, so the bound is the range and not a blanket refusal.
        good = SeekRequest()
        good.target.FromNanoseconds((range_start + range_end) // 2)
        ok, err = _call_rpc(replayer_session, "seek", good.SerializeToString())
        assert not err, _err_text(err) if err else ""
    finally:
        proc.stop()


@pytest.mark.e2e
def test_playhead_never_shows_the_file_start_while_a_range_is_set(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """`stop` and a restart from STOPPED park the playhead on the RANGE start.

    Playback was already correct -- the walker falls through to the range start either way -- but
    the status broadcast reported the FILE start until the first message emitted, so every station
    watching flicked to 0:00 and then jumped to the in-point on every stop.
    """
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
        start_ns = s.start_time.ToNanoseconds()
        end_ns = s.end_time.ToNanoseconds()
        range_start = start_ns + (end_ns - start_ns) // 2

        req = SetRangeRequest()
        req.start.FromNanoseconds(range_start)
        ok, err = _call_rpc(replayer_session, "set_range", req.SerializeToString())
        assert not err, _err_text(err) if err else ""

        # Loop on, so the replay keeps running while we stop and restart it. Half of first.mcap is
        # ~0.5 s at 1x; without the loop it would reach EOF before the stop under test.
        lreq = SetLoopRequest()
        lreq.loop = True
        ok, err = _call_rpc(replayer_session, "set_loop", lreq.SerializeToString())
        assert not err, _err_text(err) if err else ""

        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        # This collector has been subscribed since before the daemon started, so it cannot miss the
        # transition the way a freshly-declared subscriber can.
        assert collector.wait_for(
            lambda x: x.state == PubReplayStatus.PLAYING, timeout=6.0
        ), "never observed PLAYING"
        time.sleep(0.3)

        # Deterministic: _handle_stop publishes an immediate sample before returning.
        collector.clear()
        ok, err = _call_rpc(replayer_session, "stop")
        assert not err, _err_text(err) if err else ""
        stopped = collector.wait_for(
            lambda x: x.state == PubReplayStatus.STOPPED and x.HasField("current_time"),
            timeout=6.0,
        )
        assert stopped is not None, "no STOPPED sample with a current_time"
        assert stopped.current_time.ToNanoseconds() == range_start, (
            f"stop parked the playhead at {stopped.current_time.ToNanoseconds()}, "
            f"not at the range start {range_start}"
        )

        # And the restart from STOPPED does the same. Asserted over every sample, because the
        # flick lasts only until the first message of the span emits.
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        time.sleep(1.5)
        times = [
            x.current_time.ToNanoseconds()
            for x in collector.messages
            if x.HasField("current_time")
        ]
        assert times, "no samples with a current_time observed"
        assert min(times) >= range_start, (
            f"playhead reported {min(times)}, before the range start {range_start} "
            "-- stop or play rewound to the file start"
        )
    finally:
        sub.undeclare()
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


# The fixture writers' timeline, restated rather than imported: 50 ms apart from
# a fixed epoch. The assertions below must not derive their expectation from the
# code they check.
_FIXTURE_BASE_NS = 1_700_000_000 * 1_000_000_000
_FIXTURE_PERIOD_NS = 50 * 1_000_000


def _describe(session, path: str):
    ok, err = _call_rpc(
        session,
        "describe_file",
        DescribeFileRequest(path=path).SerializeToString(),
        timeout=5.0,
    )
    return ok, err


@pytest.mark.e2e
def test_describe_file_counts_and_bounds_without_loading(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """A file can be described before it is loaded, and its per-channel count
    and first/last times are exact — from the summary and the message indexes,
    not the chunk bounds."""
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _describe(replayer_session, "first.mcap")
        assert not err, _err_text(err) if err else ""
        resp = DescribeFileResponse()
        resp.ParseFromString(_ok_payload(ok))

        assert resp.file.path == "first.mcap"
        assert resp.file.message_count == 20
        assert not resp.from_scan
        assert len(resp.channels) == 1
        ch = resp.channels[0]
        assert ch.topic == f"{REALM}/@v0/fixture/pubsub/raw/source"
        assert ch.schema_name == "test/Bytes"
        assert ch.message_count == 20
        assert ch.first_time.ToNanoseconds() == _FIXTURE_BASE_NS
        assert (
            ch.last_time.ToNanoseconds() == _FIXTURE_BASE_NS + 19 * _FIXTURE_PERIOD_NS
        )
    finally:
        proc.stop()


@pytest.mark.e2e
def test_describe_file_without_statistics_reads_end_to_end(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """No statistics means no per-channel counts in the summary, so the replayer
    reads the file through — and says so with from_scan, figures still exact."""
    d = temp_dir / "nostats-describe"
    d.mkdir()
    n = _make_no_summary_mcap(d / "nostats.mcap", n_messages=15)
    proc = _start_replayer(connector_process_factory, d, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _describe(replayer_session, "nostats.mcap")
        assert not err, _err_text(err) if err else ""
        resp = DescribeFileResponse()
        resp.ParseFromString(_ok_payload(ok))
        assert resp.from_scan
        assert sum(c.message_count for c in resp.channels) == n
        ch = next(c for c in resp.channels if c.message_count)
        assert ch.last_time.ToNanoseconds() > ch.first_time.ToNanoseconds() > 0
    finally:
        proc.stop()


@pytest.mark.e2e
def test_describe_file_refuses_escapes_and_missing_files(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """describe_file shares load_file's path guard, so it cannot be used to read
    outside the base directory."""
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _describe(replayer_session, "../escape.mcap")
        assert err, "expected an error for a path outside the base directory"
        assert _err_code(err) == ErrorResponse.Code.PERMISSION_DENIED
        ok, err = _describe(replayer_session, "missing.mcap")
        assert err, "expected an error for a missing file"
        assert _err_code(err) == ErrorResponse.Code.NOT_FOUND
    finally:
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


@pytest.mark.e2e
def test_replay_key_tag_publishes_on_suffixed_keys(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """--replay-key-tag republishes each message on ``<topic>/replay`` and leaves
    the original key silent, so a replay can't be mistaken for live data on the
    bus. Asserts the suffix on the wire, not just that the load succeeded."""
    d = temp_dir / "tagged_wire"
    d.mkdir()
    topic = f"{REALM}/@v0/fixture/pubsub/raw/source"
    _make_fixture_mcap(d / "one.mcap", n_messages=10, topic=topic)

    on_plain: list[int] = []
    on_replay: list[int] = []
    sub_plain = replayer_session.declare_subscriber(
        topic, lambda _s: on_plain.append(1)
    )
    sub_replay = replayer_session.declare_subscriber(
        topic + "/replay", lambda _s: on_replay.append(1)
    )

    proc = _start_replayer(
        connector_process_factory,
        d,
        zenoh_endpoints,
        mcap_file=d / "one.mcap",
        extra=["--replay-key-tag", "--start-paused"],
    )
    try:
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED, timeout=6.0)
        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)
        time.sleep(0.5)

        assert len(on_replay) == 10, f"expected 10 on /replay, got {len(on_replay)}"
        assert len(on_plain) == 0, f"expected 0 on the plain key, got {len(on_plain)}"
    finally:
        sub_plain.undeclare()
        sub_replay.undeclare()
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


# ----------------------------------------------------------------------------
# Several files loaded as one (LoadFileRequest.paths)
# ----------------------------------------------------------------------------


def _make_labelled_mcap(
    path: Path,
    topic: str,
    label: str,
    n_messages: int,
    offset_ms: int,
    period_ms: int = 100,
) -> list[bytes]:
    """One channel, payloads ``f"{label}#{i}"``, starting ``offset_ms`` after the shared epoch.

    Two of these with offsets 0 and period/2 interleave exactly, so a merged replay has one
    correct order and anything else is visibly wrong. Returns the payloads in log-time order.
    """
    payloads = []
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        sid = writer.register_schema(name="test/Bytes", encoding="raw", data=b"")
        cid = writer.register_channel(
            schema_id=sid, topic=topic, message_encoding="raw"
        )
        base_ns = 1_700_000_000 * 1_000_000_000
        for i in range(n_messages):
            t = base_ns + (offset_ms + i * period_ms) * 1_000_000
            payload = f"{label}#{i}".encode()
            writer.add_message(
                channel_id=cid, log_time=t, publish_time=t, sequence=i, data=payload
            )
            payloads.append(payload)
        writer.finish()
    return payloads


def _load_paths(session: zenoh.Session, paths: list[str]):
    return _call_rpc(
        session,
        "load_file",
        LoadFileRequest(path=paths[0], paths=paths).SerializeToString(),
    )


@pytest.mark.e2e
def test_load_several_files_interleaves_them_by_log_time(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """Two recordings of one topic, offset by half a period, replay as one stream in log-time
    order -- not one file then the other -- and the status covers both."""
    d = temp_dir / "merge"
    d.mkdir()
    topic = f"{REALM}/@v0/fixture/pubsub/raw/src"
    a = _make_labelled_mcap(d / "a.mcap", topic, "a", 6, offset_ms=0)
    b = _make_labelled_mcap(d / "b.mcap", topic, "b", 6, offset_ms=50)
    expected = [m for pair in zip(a, b) for m in pair]

    received: list[bytes] = []
    sub = replayer_session.declare_subscriber(
        topic,
        lambda s: received.append(keelson.uncover(s.payload.to_bytes())[2]),
    )
    proc = _start_replayer(connector_process_factory, d, zenoh_endpoints)
    try:
        s = _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        assert s.daemon.multi_file_load

        ok, err = _load_paths(replayer_session, ["a.mcap", "b.mcap"])
        assert not err, _err_text(err) if err else ""
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        assert [Path(p).name for p in s.loaded_files] == ["a.mcap", "b.mcap"]
        assert s.loaded_file.endswith("a.mcap")
        assert s.total_message_count == 12
        # The window is the union: a's first message to b's last.
        assert (
            s.end_time.ToNanoseconds() - s.start_time.ToNanoseconds()
            == (50 + 5 * 100) * 1_000_000
        )

        ok, err = _call_rpc(replayer_session, "play")
        assert not err, _err_text(err) if err else ""
        end = _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)
        time.sleep(0.3)
        assert end.played_message_count == 12
        assert received == expected
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_merged_files_keep_each_channel_on_its_own_key(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """Both files number their only channel 1. Publishers keyed by channel id would send the
    second file's messages out on the first file's key; keyed by topic, each keeps its own.
    """
    d = temp_dir / "merge_keys"
    d.mkdir()
    topic_a = f"{REALM}/@v0/fixture/pubsub/channel_a/src"
    topic_b = f"{REALM}/@v0/fixture/pubsub/channel_b/src"
    a = _make_labelled_mcap(d / "a.mcap", topic_a, "a", 4, offset_ms=0)
    b = _make_labelled_mcap(d / "b.mcap", topic_b, "b", 4, offset_ms=50)

    received: dict[str, list[bytes]] = {topic_a: [], topic_b: []}
    subs = [
        replayer_session.declare_subscriber(
            t,
            lambda s, t=t: received[t].append(keelson.uncover(s.payload.to_bytes())[2]),
        )
        for t in (topic_a, topic_b)
    ]
    proc = _start_replayer(connector_process_factory, d, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _load_paths(replayer_session, ["a.mcap", "b.mcap"])
        assert not err, _err_text(err) if err else ""
        _wait_for_state(replayer_session, PubReplayStatus.PAUSED)
        _call_rpc(replayer_session, "play")
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)
        time.sleep(0.3)
        assert received[topic_a] == a
        assert received[topic_b] == b
    finally:
        for sub in subs:
            sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_seek_into_merged_files_resumes_from_both(
    connector_process_factory, temp_dir, zenoh_endpoints, replayer_session
):
    """A seek lands in both files at once: the next message is whichever of them is due first."""
    d = temp_dir / "merge_seek"
    d.mkdir()
    topic = f"{REALM}/@v0/fixture/pubsub/raw/src"
    a = _make_labelled_mcap(d / "a.mcap", topic, "a", 6, offset_ms=0)
    b = _make_labelled_mcap(d / "b.mcap", topic, "b", 6, offset_ms=50)

    received: list[bytes] = []
    sub = replayer_session.declare_subscriber(
        topic,
        lambda s: received.append(keelson.uncover(s.payload.to_bytes())[2]),
    )
    proc = _start_replayer(connector_process_factory, d, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        _load_paths(replayer_session, ["a.mcap", "b.mcap"])
        s = _wait_for_state(replayer_session, PubReplayStatus.PAUSED)

        # b#3 is at 350 ms: the merged stream from there is b#3, a#4, b#4, a#5, b#5.
        req = SeekRequest()
        req.target.FromNanoseconds(s.start_time.ToNanoseconds() + 350 * 1_000_000)
        ok, err = _call_rpc(replayer_session, "seek", req.SerializeToString())
        assert not err, _err_text(err) if err else ""
        _call_rpc(replayer_session, "play")
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED, timeout=8.0)
        time.sleep(0.3)
        assert received == [b[3], a[4], b[4], a[5], b[5]]
    finally:
        sub.undeclare()
        proc.stop()


@pytest.mark.e2e
def test_load_several_files_refuses_all_on_one_bad_path(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    """One missing file refuses the whole load -- not the files listed before it."""
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _load_paths(replayer_session, ["first.mcap", "missing.mcap"])
        assert not ok
        assert _err_code(err) == ErrorResponse.Code.NOT_FOUND
        s = _latest_status(replayer_session)
        assert s.state == PubReplayStatus.STOPPED
        assert not s.loaded_files
    finally:
        proc.stop()
