"""Unit tests for rorkult.health.

HealthState is a small state machine + a few counters; build_entity_health
is pure. Both are fully covered here so the e2e test only has to confirm
end-to-end wiring through Zenoh.
"""

import time

from keelson.payloads.EntityHealth_pb2 import HealthLevel

from rorkult.health import HealthState, Snapshot, build_entity_health


# ---- HealthState ---------------------------------------------------------


class TestInitialSnapshot:
    def test_starts_critical_with_never_connected_detail(self):
        snap = HealthState().snapshot()
        assert snap.level == HealthLevel.HEALTH_CRITICAL
        assert "no successful connect" in snap.detail

    def test_counters_start_at_zero(self):
        snap = HealthState().snapshot()
        assert snap.connect_attempts_since_success == 0
        assert snap.bytes_received_total == 0
        assert snap.last_byte_received_ns is None


class TestStateTransitions:
    def test_mark_connected_goes_nominal_with_detail(self):
        state = HealthState()
        state.mark_connected("192.0.2.50:9000")
        snap = state.snapshot()
        assert snap.level == HealthLevel.HEALTH_NOMINAL
        assert "192.0.2.50:9000" in snap.detail

    def test_mark_disconnected_goes_critical_with_reason(self):
        state = HealthState()
        state.mark_connected("a:1")
        state.mark_disconnected("read failed: EOF")
        snap = state.snapshot()
        assert snap.level == HealthLevel.HEALTH_CRITICAL
        assert snap.detail == "read failed: EOF"

    def test_repeated_mark_connected_is_idempotent(self):
        state = HealthState()
        state.mark_connected("a:1")
        state.mark_connected("a:1")
        assert state.snapshot().level == HealthLevel.HEALTH_NOMINAL


class TestConnectAttempts:
    def test_attempt_counter_increments_on_each_call(self):
        state = HealthState()
        state.mark_connect_attempt()
        state.mark_connect_attempt()
        state.mark_connect_attempt()
        assert state.snapshot().connect_attempts_since_success == 3

    def test_attempt_counter_resets_on_successful_connect(self):
        state = HealthState()
        for _ in range(5):
            state.mark_connect_attempt()
        state.mark_connected("a:1")
        assert state.snapshot().connect_attempts_since_success == 0

    def test_attempts_after_disconnect_climb_again(self):
        state = HealthState()
        state.mark_connected("a:1")
        state.mark_disconnected("dropped")
        state.mark_connect_attempt()
        state.mark_connect_attempt()
        assert state.snapshot().connect_attempts_since_success == 2


class TestBytesReceived:
    def test_total_accumulates(self):
        state = HealthState()
        state.mark_bytes_received(64)
        state.mark_bytes_received(128)
        assert state.snapshot().bytes_received_total == 192

    def test_total_survives_reconnect(self):
        """bytes_received is process-lifetime, NOT per-connection."""
        state = HealthState()
        state.mark_bytes_received(50)
        state.mark_disconnected("drop")
        state.mark_connected("a:1")
        state.mark_bytes_received(25)
        assert state.snapshot().bytes_received_total == 75

    def test_zero_or_negative_is_a_no_op(self):
        state = HealthState()
        state.mark_bytes_received(0)
        state.mark_bytes_received(-5)
        snap = state.snapshot()
        assert snap.bytes_received_total == 0
        assert snap.last_byte_received_ns is None

    def test_last_byte_timestamp_advances(self):
        state = HealthState()
        before = time.time_ns()
        state.mark_bytes_received(1)
        after = time.time_ns()
        snap = state.snapshot()
        assert snap.last_byte_received_ns is not None
        assert before <= snap.last_byte_received_ns <= after


# ---- build_entity_health -------------------------------------------------


def _checks(msg):
    """Return checks of the only SubjectHealth as a name -> CheckResult dict."""
    return {c.name: c for c in msg.sources[0].subjects[0].checks}


class TestBuildEntityHealthShape:
    def test_top_level_fields(self):
        state = HealthState()
        state.mark_connected("127.0.0.1:9000")
        msg = build_entity_health(
            state, publish_rate_hz=2.5, timestamp_ns=1_700_000_000_000_000_000
        )
        assert msg.level == HealthLevel.HEALTH_NOMINAL
        assert msg.rate_hz == 2.5
        assert msg.timestamp.ToNanoseconds() == 1_700_000_000_000_000_000

    def test_source_and_subject_naming(self):
        msg = build_entity_health(HealthState(), publish_rate_hz=1.0)
        assert len(msg.sources) == 1
        assert msg.sources[0].name == "mcu_link"
        assert len(msg.sources[0].subjects) == 1
        assert msg.sources[0].subjects[0].name == "tcp_connection"

    def test_all_four_checks_emitted(self):
        msg = build_entity_health(HealthState(), publish_rate_hz=1.0)
        checks = _checks(msg)
        assert set(checks.keys()) == {
            "connected",
            "connect_attempts_since_success",
            "bytes_received_total",
            "last_byte_received",
        }


class TestBuildEntityHealthConnectedCheck:
    def test_connected_check_critical_when_never_connected(self):
        msg = build_entity_health(HealthState(), publish_rate_hz=1.0)
        c = _checks(msg)["connected"]
        assert c.level == HealthLevel.HEALTH_CRITICAL
        assert "no successful connect" in c.detail

    def test_connected_check_nominal_with_endpoint(self):
        state = HealthState()
        state.mark_connected("127.0.0.1:9000")
        msg = build_entity_health(state, publish_rate_hz=1.0)
        c = _checks(msg)["connected"]
        assert c.level == HealthLevel.HEALTH_NOMINAL
        assert "127.0.0.1:9000" in c.detail


class TestBuildEntityHealthMetricChecks:
    def test_connect_attempts_check_reflects_state(self):
        state = HealthState()
        for _ in range(7):
            state.mark_connect_attempt()
        msg = build_entity_health(state, publish_rate_hz=1.0)
        c = _checks(msg)["connect_attempts_since_success"]
        # Metrics emit at NOMINAL today (informational).
        assert c.level == HealthLevel.HEALTH_NOMINAL
        assert c.detail == "7"

    def test_bytes_received_total_check_reflects_state(self):
        state = HealthState()
        state.mark_bytes_received(1024)
        msg = build_entity_health(state, publish_rate_hz=1.0)
        c = _checks(msg)["bytes_received_total"]
        assert c.level == HealthLevel.HEALTH_NOMINAL
        assert c.detail == "1024"

    def test_last_byte_received_says_never_until_any_data(self):
        msg = build_entity_health(HealthState(), publish_rate_hz=1.0)
        c = _checks(msg)["last_byte_received"]
        assert c.level == HealthLevel.HEALTH_NOMINAL
        assert c.detail == "never"

    def test_last_byte_received_age_after_data(self):
        state = HealthState()
        state.mark_bytes_received(10)
        msg = build_entity_health(
            state, publish_rate_hz=1.0, timestamp_ns=time.time_ns()
        )
        c = _checks(msg)["last_byte_received"]
        assert c.level == HealthLevel.HEALTH_NOMINAL
        assert c.detail.endswith("s ago")


class TestBuildEntityHealthDefaults:
    def test_timestamp_defaults_to_now(self):
        before = time.time_ns()
        msg = build_entity_health(HealthState(), publish_rate_hz=1.0)
        after = time.time_ns()
        assert before <= msg.timestamp.ToNanoseconds() <= after


# ---- Snapshot dataclass smoke -------------------------------------------


def test_snapshot_is_frozen():
    snap = HealthState().snapshot()
    assert isinstance(snap, Snapshot)
    # frozen=True dataclass: assignment raises FrozenInstanceError.
    import dataclasses

    try:
        snap.connect_attempts_since_success = 99  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Snapshot should be frozen")
