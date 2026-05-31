"""Unit tests for rorkult.health.

HealthState is a small state machine; build_entity_health is pure.
Both are fully covered here so the e2e test only has to confirm
end-to-end wiring through Zenoh.
"""

from keelson.payloads.EntityHealth_pb2 import HealthLevel

from rorkult.health import HealthState, build_entity_health


# ---- HealthState ---------------------------------------------------------


def test_initial_state_is_critical_with_never_connected_detail():
    state = HealthState()
    level, detail = state.snapshot()
    assert level == HealthLevel.HEALTH_CRITICAL
    assert "no successful connect" in detail


def test_mark_connected_transitions_to_nominal():
    state = HealthState()
    state.mark_connected("192.0.2.50:9000")
    level, detail = state.snapshot()
    assert level == HealthLevel.HEALTH_NOMINAL
    assert "192.0.2.50:9000" in detail


def test_mark_disconnected_returns_to_critical_with_reason():
    state = HealthState()
    state.mark_connected("192.0.2.50:9000")
    state.mark_disconnected("read failed: EOF")
    level, detail = state.snapshot()
    assert level == HealthLevel.HEALTH_CRITICAL
    assert detail == "read failed: EOF"


def test_repeated_mark_connected_is_idempotent():
    state = HealthState()
    state.mark_connected("a:1")
    state.mark_connected("a:1")
    assert state.snapshot() == (HealthLevel.HEALTH_NOMINAL, "connected to a:1")


# ---- build_entity_health -------------------------------------------------


def test_build_entity_health_nominal_shape():
    state = HealthState()
    state.mark_connected("127.0.0.1:9000")
    msg = build_entity_health(state, publish_rate_hz=1.0, timestamp_ns=1_700_000_000_000_000_000)

    assert msg.level == HealthLevel.HEALTH_NOMINAL
    assert msg.rate_hz == 1.0
    assert msg.timestamp.ToNanoseconds() == 1_700_000_000_000_000_000

    assert len(msg.sources) == 1
    src = msg.sources[0]
    assert src.name == "mcu_link"
    assert src.level == HealthLevel.HEALTH_NOMINAL

    assert len(src.subjects) == 1
    sub = src.subjects[0]
    assert sub.name == "tcp_connection"
    assert sub.level == HealthLevel.HEALTH_NOMINAL

    assert len(sub.checks) == 1
    chk = sub.checks[0]
    assert chk.name == "connected"
    assert chk.level == HealthLevel.HEALTH_NOMINAL
    assert "127.0.0.1:9000" in chk.detail


def test_build_entity_health_critical_propagates_reason():
    state = HealthState()
    state.mark_disconnected("connect refused")
    msg = build_entity_health(state, publish_rate_hz=2.0)

    assert msg.level == HealthLevel.HEALTH_CRITICAL
    assert msg.rate_hz == 2.0
    assert msg.sources[0].level == HealthLevel.HEALTH_CRITICAL
    assert msg.sources[0].subjects[0].level == HealthLevel.HEALTH_CRITICAL
    assert msg.sources[0].subjects[0].checks[0].detail == "connect refused"


def test_build_entity_health_timestamp_defaults_to_now():
    import time as _time

    state = HealthState()
    before_ns = _time.time_ns()
    msg = build_entity_health(state, publish_rate_hz=1.0)
    after_ns = _time.time_ns()
    ts_ns = msg.timestamp.ToNanoseconds()
    assert before_ns <= ts_ns <= after_ns
