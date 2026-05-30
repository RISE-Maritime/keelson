"""End-to-end smoke tests for keelson2rorkult.

Launches the connector as a real subprocess (against an offline MCU,
since the connector should retry indefinitely and not crash), verifies
it declares its RPC queryables, exercises a stubbed RPC, and confirms
clean shutdown on SIGINT.
"""

import os
import sys
import time
from pathlib import Path

import pytest
import zenoh

from keelson import construct_rpc_key
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.interfaces.VehicleControl_pb2 import ControlAxisMapping
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest,
    ArmResponse,
)


pytestmark = pytest.mark.e2e


REALM = "test"
ENTITY = "rover-1"
SOURCE = "rorkult/0"


def _connector_env() -> dict:
    """Augment PYTHONPATH so the rorkult sub-package resolves in the
    subprocess (matches the in-bin sys.path bootstrap, but earlier so
    Python's importer sees it during module init)."""
    env = os.environ.copy()
    repo = Path(__file__).resolve().parents[3]
    extras = [
        str(repo / "sdks" / "python"),
        str(repo / "connectors" / "rorkult"),
    ]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = ":".join(extras + ([existing] if existing else []))
    return env


def _wait_for_log(proc, marker: str, timeout: float = 5.0) -> bool:
    """Poll the process's accumulated stderr for ``marker`` without
    consuming it (so other assertions can also inspect logs)."""
    # We can't peek stderr without draining; instead, attach a side
    # buffer that the test reads at stop() time. Simpler heuristic for
    # the skeleton: wait a fixed grace period and assert at stop().
    time.sleep(timeout)
    return True  # see _assert_marker_in_logs below


def _assert_marker_in_logs(stderr: str, marker: str) -> None:
    assert marker in stderr, (
        f"Expected log marker {marker!r} not found.\n"
        f"---- stderr ----\n{stderr}\n"
    )


def _rpc_call(session, key: str, request_bytes: bytes, timeout: float = 5.0):
    """Synchronous RPC helper: send a query, return the first reply."""
    replies = []

    def _on_reply(reply):
        replies.append(reply)

    session.get(key, _on_reply, payload=request_bytes)
    deadline = time.time() + timeout
    while time.time() < deadline and not replies:
        time.sleep(0.05)
    if not replies:
        raise TimeoutError(f"RPC {key} did not reply within {timeout}s")
    return replies[0]


@pytest.fixture
def connector_proc(connector_process_factory, zenoh_endpoints):
    """Run keelson2rorkult against an offline MCU port (port 1) so it
    enters the reconnect loop immediately — the connector should stay
    up regardless."""
    proc = connector_process_factory(
        "rorkult",
        "keelson2rorkult",
        [
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
            "--realm",
            REALM,
            "--entity-id",
            ENTITY,
            "--source-id",
            SOURCE,
            "--mcu-endpoint",
            "127.0.0.1:1",  # virtually always refused -> exercise backoff
            "--mcu-connect-timeout-s",
            "0.3",
            "--mcu-reconnect-backoff-s",
            "0.1,0.5",
        ],
    )
    proc.env = _connector_env()
    proc.start()
    # Give the connector a beat to open Zenoh + declare queryables.
    time.sleep(2.0)
    yield proc
    proc.stop(timeout=5.0)


def test_connector_starts_and_logs_liveliness(connector_proc):
    connector_proc.stop(timeout=5.0)
    _stdout, stderr = connector_proc.logs()
    _assert_marker_in_logs(stderr, "Declared liveliness token")
    # All five RPC queryables should have come up.
    for proc_name in (
        "set_control_mapping",
        "get_control_mapping",
        "arm",
        "set_mode",
        "emergency_stop",
    ):
        _assert_marker_in_logs(stderr, f"Declared RPC queryable")
        _assert_marker_in_logs(stderr, proc_name)


def test_connector_survives_unreachable_mcu(connector_proc):
    """The connector should still be running after the backoff loop has
    had a chance to fail a few times."""
    time.sleep(1.5)  # give the supervisor a couple of failed attempts
    assert connector_proc.is_running()
    connector_proc.stop(timeout=5.0)
    _stdout, stderr = connector_proc.logs()
    _assert_marker_in_logs(stderr, "MCU connect to 127.0.0.1:1 failed")


def test_arm_rpc_returns_unsupported(connector_proc, zenoh_endpoints):
    """Stubbed VehicleLifecycle.arm should respond with UNSUPPORTED +
    the documented detail string until framing lands."""
    zconf = zenoh.Config()
    import json as _json

    zconf.insert_json5("mode", _json.dumps("peer"))
    zconf.insert_json5(
        "connect/endpoints", _json.dumps([zenoh_endpoints["connect"]])
    )
    with zenoh.open(zconf) as session:
        time.sleep(0.5)  # let scout/discovery converge
        key = construct_rpc_key(REALM, ENTITY, "arm", SOURCE)
        req = ArmRequest(arm=True)
        req.timestamp.GetCurrentTime()
        reply = _rpc_call(session, key, req.SerializeToString(), timeout=5.0)
        try:
            ok = reply.ok
        except Exception:
            ok = None
        assert ok is not None, "expected a normal reply, got an err"
        resp = ArmResponse()
        resp.ParseFromString(bytes(ok.payload.to_bytes()))
        assert resp.result == CommandResult.COMMAND_RESULT_UNSUPPORTED
        assert "framing not yet implemented" in resp.detail


def test_set_control_mapping_returns_err(connector_proc, zenoh_endpoints):
    """The Ack response has no result/detail field, so the stub
    handler uses reply_err with the framing message."""
    zconf = zenoh.Config()
    import json as _json

    zconf.insert_json5("mode", _json.dumps("peer"))
    zconf.insert_json5(
        "connect/endpoints", _json.dumps([zenoh_endpoints["connect"]])
    )
    with zenoh.open(zconf) as session:
        time.sleep(0.5)
        key = construct_rpc_key(REALM, ENTITY, "set_control_mapping", SOURCE)
        req = ControlAxisMapping()
        reply = _rpc_call(session, key, req.SerializeToString(), timeout=5.0)
        # Expect err, not ok.
        try:
            err = reply.err
        except Exception:
            err = None
        assert err is not None, "expected an err reply, got ok"
        msg = ErrorResponse()
        msg.ParseFromString(bytes(err.payload.to_bytes()))
        assert "framing not yet implemented" in msg.error_description
