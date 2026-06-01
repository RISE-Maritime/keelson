"""End-to-end smoke tests for keelson2rorkult.

Launches the connector as a real subprocess (against an offline MCU,
since the connector should retry indefinitely and not crash), verifies
it declares its RPC queryables, exercises a stubbed RPC, and confirms
clean shutdown on SIGINT.
"""

import json
import os
import time
from pathlib import Path

import pytest
import zenoh

from keelson import construct_pubsub_key, construct_rpc_key, uncover
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.interfaces.VehicleControl_pb2 import (
    ControlAxis,
    ControlAxisMapping,
    ControlAxisMappingAck,
)
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest,
    ArmResponse,
)
from keelson.payloads.EntityHealth_pb2 import EntityHealth, HealthLevel


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
        f"Expected log marker {marker!r} not found.\n" f"---- stderr ----\n{stderr}\n"
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


def _connector_args(zenoh_endpoints, mcu_port: int) -> list[str]:
    return [
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
        f"127.0.0.1:{mcu_port}",
        "--mcu-connect-timeout-s",
        "0.3",
        "--mcu-reconnect-backoff-s",
        "0.1,0.5",
        "--health-publish-rate-hz",
        "5.0",  # quicker than default so tests don't have to wait a full second
    ]


@pytest.fixture
def connector_proc(connector_process_factory, zenoh_endpoints):
    """Run keelson2rorkult against an offline MCU port (port 1) so it
    enters the reconnect loop immediately — the connector should stay
    up regardless."""
    proc = connector_process_factory(
        "rorkult",
        "keelson2rorkult",
        _connector_args(zenoh_endpoints, mcu_port=1),
    )
    proc.env = _connector_env()
    proc.start()
    # Give the connector a beat to open Zenoh + declare queryables.
    time.sleep(2.0)
    yield proc
    proc.stop(timeout=5.0)


@pytest.fixture
def connector_proc_with_mcu(connector_process_factory, zenoh_endpoints, mock_mcu):
    """Run keelson2rorkult against the mock MCU so the supervisor
    successfully connects and HealthState becomes NOMINAL."""
    proc = connector_process_factory(
        "rorkult",
        "keelson2rorkult",
        _connector_args(zenoh_endpoints, mcu_port=mock_mcu.port),
    )
    proc.env = _connector_env()
    proc.start()
    # Beat for Zenoh + at least one MCU connect.
    time.sleep(2.0)
    yield proc
    proc.stop(timeout=5.0)


def _wait_for_sample(session, key: str, timeout: float = 5.0):
    """Subscribe to ``key`` and return the first sample within ``timeout``.

    Uses the simple list-append callback pattern from other e2e tests so
    the wait window is bounded.
    """
    samples: list = []

    def _cb(sample):
        samples.append(sample)

    sub = session.declare_subscriber(key, _cb)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline and not samples:
            time.sleep(0.05)
        return samples[0] if samples else None
    finally:
        try:
            sub.undeclare()
        except Exception:
            pass


def _peer_session(zenoh_endpoints) -> zenoh.Session:
    """Open a peer Zenoh session connected to the connector's listener."""
    zconf = zenoh.Config()
    zconf.insert_json5("mode", json.dumps("peer"))
    zconf.insert_json5("connect/endpoints", json.dumps([zenoh_endpoints["connect"]]))
    return zenoh.open(zconf)


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
        _assert_marker_in_logs(stderr, "Declared RPC queryable")
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
    with _peer_session(zenoh_endpoints) as session:
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


def test_set_control_mapping_accepts_valid_mapping(connector_proc, zenoh_endpoints):
    """A valid steering+throttle mapping should be accepted with an Ack
    (real now that ControlAxisState is wired in)."""
    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.5)
        key = construct_rpc_key(REALM, ENTITY, "set_control_mapping", SOURCE)
        req = ControlAxisMapping(
            axes={
                "steering": ControlAxis(
                    subject="joystick_x_pct", source_id="gamepad-1"
                ),
                "throttle": ControlAxis(
                    subject="joystick_y_pct", source_id="gamepad-1"
                ),
            },
            min_interval_s=0.05,
        )
        reply = _rpc_call(session, key, req.SerializeToString(), timeout=5.0)
        try:
            ok = reply.ok
        except Exception:
            ok = None
        assert ok is not None, "expected an ok reply (Ack), got err"
        # ControlAxisMappingAck has no fields; parse to verify wire format.
        ControlAxisMappingAck().ParseFromString(bytes(ok.payload.to_bytes()))


def test_set_control_mapping_rejects_unknown_axis(connector_proc, zenoh_endpoints):
    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.5)
        key = construct_rpc_key(REALM, ENTITY, "set_control_mapping", SOURCE)
        req = ControlAxisMapping(
            axes={
                "wibble": ControlAxis(subject="joystick_x_pct", source_id="gamepad-1"),
            },
        )
        reply = _rpc_call(session, key, req.SerializeToString(), timeout=5.0)
        try:
            err = reply.err
        except Exception:
            err = None
        assert err is not None, "expected an err reply for unknown axis"
        msg = ErrorResponse()
        msg.ParseFromString(bytes(err.payload.to_bytes()))
        assert "unknown axis" in msg.error_description


def test_set_control_mapping_rejects_loopback(connector_proc, zenoh_endpoints):
    """An axis subscribing to the connector's own source_id is a loopback
    -- the guard fires before any subscribers are installed."""
    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.5)
        key = construct_rpc_key(REALM, ENTITY, "set_control_mapping", SOURCE)
        req = ControlAxisMapping(
            axes={
                "steering": ControlAxis(
                    subject="joystick_x_pct",
                    source_id=SOURCE,  # connector's own source_id
                    entity_id=ENTITY,
                ),
            },
        )
        reply = _rpc_call(session, key, req.SerializeToString(), timeout=5.0)
        try:
            err = reply.err
        except Exception:
            err = None
        assert err is not None, "expected an err reply for loopback"
        msg = ErrorResponse()
        msg.ParseFromString(bytes(err.payload.to_bytes()))
        assert "loopback" in msg.error_description.lower()


def test_get_control_mapping_returns_installed_mapping(connector_proc, zenoh_endpoints):
    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.5)
        # Install a mapping first.
        set_key = construct_rpc_key(REALM, ENTITY, "set_control_mapping", SOURCE)
        set_req = ControlAxisMapping(
            axes={
                "throttle": ControlAxis(
                    subject="joystick_y_pct",
                    source_id="gamepad-99",
                    invert=True,
                ),
            },
        )
        _rpc_call(session, set_key, set_req.SerializeToString(), timeout=5.0)

        # Round-trip via get_control_mapping.
        get_key = construct_rpc_key(REALM, ENTITY, "get_control_mapping", SOURCE)
        reply = _rpc_call(session, get_key, b"", timeout=5.0)
        try:
            ok = reply.ok
        except Exception:
            ok = None
        assert ok is not None, "expected an ok reply, got err"
        got = ControlAxisMapping()
        got.ParseFromString(bytes(ok.payload.to_bytes()))
        assert "throttle" in got.axes
        assert got.axes["throttle"].subject == "joystick_y_pct"
        assert got.axes["throttle"].source_id == "gamepad-99"
        assert got.axes["throttle"].invert is True
        # Effective dead-man default surfaces in the get response.
        assert got.max_axis_age_s == 1.0


# ---- entity_health publishing -------------------------------------------


def _read_entity_health(sample) -> EntityHealth:
    """Unwrap the Keelson envelope around a sample and parse the payload."""
    payload_bytes = bytes(sample.payload.to_bytes())
    _enclosed_at, _received_at, payload = uncover(payload_bytes)
    msg = EntityHealth()
    msg.ParseFromString(payload)
    return msg


def _checks_by_name(msg) -> dict:
    """Return SubjectHealth.checks as a name -> CheckResult dict for assertions."""
    return {c.name: c for c in msg.sources[0].subjects[0].checks}


def test_entity_health_published_critical_when_mcu_unreachable(
    connector_proc, zenoh_endpoints
):
    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.5)
        key = construct_pubsub_key(REALM, ENTITY, "entity_health", SOURCE)
        sample = _wait_for_sample(session, key, timeout=5.0)
        assert sample is not None, "no entity_health sample received within 5s"
        msg = _read_entity_health(sample)
        assert msg.level == HealthLevel.HEALTH_CRITICAL
        assert msg.rate_hz == 5.0

        assert len(msg.sources) == 1
        src = msg.sources[0]
        assert src.name == "mcu_link"
        assert src.level == HealthLevel.HEALTH_CRITICAL

        checks = _checks_by_name(msg)
        assert checks["connected"].level == HealthLevel.HEALTH_CRITICAL
        assert "127.0.0.1:1" in checks["connected"].detail
        # Supervisor has been retrying — connect_attempts climbs.
        assert int(checks["connect_attempts_since_success"].detail) >= 1
        # No bytes received from a port that never connected.
        assert checks["bytes_received_total"].detail == "0"
        assert checks["last_byte_received"].detail == "never"


def test_entity_health_published_nominal_when_mcu_connected(
    connector_proc_with_mcu, mock_mcu, zenoh_endpoints
):
    # Make sure the server actually accepted the connector's connect
    # before we expect NOMINAL — otherwise we can race the supervisor.
    assert mock_mcu.wait_for_connections(1, timeout=5.0)
    # And give the publisher at least one tick (5 Hz = 200 ms) to fire
    # after the connect transition.
    time.sleep(0.5)

    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.3)
        key = construct_pubsub_key(REALM, ENTITY, "entity_health", SOURCE)
        sample = _wait_for_sample(session, key, timeout=5.0)
        assert sample is not None, "no entity_health sample received within 5s"
        msg = _read_entity_health(sample)
        assert msg.level == HealthLevel.HEALTH_NOMINAL
        src = msg.sources[0]
        assert src.level == HealthLevel.HEALTH_NOMINAL

        checks = _checks_by_name(msg)
        assert checks["connected"].level == HealthLevel.HEALTH_NOMINAL
        assert f"127.0.0.1:{mock_mcu.port}" in checks["connected"].detail
        # First successful connect resets the attempts counter to 0.
        assert checks["connect_attempts_since_success"].detail == "0"


def test_entity_health_records_bytes_when_mcu_sends_data(
    connector_proc_with_mcu, mock_mcu, zenoh_endpoints
):
    """Once the supervisor is connected and the MCU sends bytes, the
    bytes_received_total and last_byte_received checks reflect it."""
    assert mock_mcu.wait_for_connections(1, timeout=5.0)
    mock_mcu.send(b"hello from the mcu")
    time.sleep(0.5)  # let the read loop drain + the publisher tick

    with _peer_session(zenoh_endpoints) as session:
        time.sleep(0.3)
        key = construct_pubsub_key(REALM, ENTITY, "entity_health", SOURCE)
        sample = _wait_for_sample(session, key, timeout=5.0)
        assert sample is not None
        msg = _read_entity_health(sample)
        checks = _checks_by_name(msg)
        # Bytes received total accumulates over time; should be at least
        # the payload we sent.
        assert int(checks["bytes_received_total"].detail) >= len(b"hello from the mcu")
        assert checks["last_byte_received"].detail.endswith("s ago")
