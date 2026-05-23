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
    SetLoopRequest,
    SetSpeedRequest,
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


def _make_fixture_mcap(path: Path, n_messages: int = 20, period_ms: int = 50) -> int:
    """Write a tiny valid MCAP with `n_messages` on a single channel.

    Returns the message count actually written.
    """
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        schema_id = writer.register_schema(name="test/Bytes", encoding="raw", data=b"")
        channel_id = writer.register_channel(
            schema_id=schema_id,
            topic=f"{REALM}/@v0/fixture/pubsub/raw/source",
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
def test_play_without_loaded_file_errors(
    connector_process_factory, fixture_dir, zenoh_endpoints, replayer_session
):
    proc = _start_replayer(connector_process_factory, fixture_dir, zenoh_endpoints)
    try:
        _wait_for_state(replayer_session, PubReplayStatus.STOPPED)
        ok, err = _call_rpc(replayer_session, "play")
        assert err, "expected error reply with no file loaded"
        assert "no file loaded" in _err_text(err)
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
    finally:
        proc.stop()
