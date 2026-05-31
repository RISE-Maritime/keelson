"""Unit tests for rorkult.control_axis.

The pure helpers (`_scale_axis_value`, `_source_id_overlaps`,
`_is_loopback`) are tested directly. `ControlAxisState` is tested
against a mock Zenoh session whose ``declare_subscriber`` captures the
callback so the test can synthesise sample arrivals without a live
broker.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

import keelson
from keelson.interfaces.VehicleControl_pb2 import (
    ControlAxis,
    ControlAxisMapping,
)
from keelson.payloads.Primitives_pb2 import TimestampedFloat

from rorkult.control_axis import (
    ControlAxisState,
    LoopbackError,
    RECOGNISED_AXES,
    _DEFAULT_MAX_AXIS_AGE_S,
    _is_loopback,
    _scale_axis_value,
    _source_id_overlaps,
)


# --------------------------------------------------------------------------
# _scale_axis_value
# --------------------------------------------------------------------------


class TestScaleAxisValue:
    def test_bipolar_neutral(self):
        assert _scale_axis_value(0.0, unipolar=False, invert=False) == 0.0

    def test_bipolar_full_forward(self):
        assert _scale_axis_value(100.0, unipolar=False, invert=False) == 1.0

    def test_bipolar_full_reverse(self):
        assert _scale_axis_value(-100.0, unipolar=False, invert=False) == -1.0

    def test_bipolar_clamps_above_range(self):
        assert _scale_axis_value(150.0, unipolar=False, invert=False) == 1.0

    def test_bipolar_clamps_below_range(self):
        assert _scale_axis_value(-200.0, unipolar=False, invert=False) == -1.0

    def test_unipolar_neutral(self):
        assert _scale_axis_value(0.0, unipolar=True, invert=False) == 0.0

    def test_unipolar_full_forward(self):
        assert _scale_axis_value(100.0, unipolar=True, invert=False) == 1.0

    def test_unipolar_negative_input_clamps_to_zero(self):
        assert _scale_axis_value(-50.0, unipolar=True, invert=False) == 0.0

    def test_invert_flips_sign(self):
        assert _scale_axis_value(50.0, unipolar=False, invert=True) == -0.5

    def test_invert_with_unipolar(self):
        assert _scale_axis_value(75.0, unipolar=True, invert=True) == -0.75


# --------------------------------------------------------------------------
# _source_id_overlaps
# --------------------------------------------------------------------------


class TestSourceIdOverlaps:
    def test_exact_match(self):
        assert _source_id_overlaps("rorkult/0", "rorkult/0") is True

    def test_no_overlap(self):
        assert _source_id_overlaps("joystick/1", "rorkult/0") is False

    def test_double_star_matches_everything(self):
        assert _source_id_overlaps("**", "rorkult/0") is True
        assert _source_id_overlaps("**", "anything/whatever") is True

    def test_single_star_matches_everything(self):
        assert _source_id_overlaps("*", "rorkult/0") is True

    def test_prefix_double_star_matches_descendants(self):
        assert _source_id_overlaps("rorkult/**", "rorkult/0") is True
        assert _source_id_overlaps("rorkult/**", "rorkult/0/setpoint") is True
        assert _source_id_overlaps("rorkult/**", "rorkult") is True

    def test_prefix_double_star_does_not_match_unrelated(self):
        assert _source_id_overlaps("rorkult/**", "other") is False

    def test_prefix_single_star_matches_one_segment(self):
        assert _source_id_overlaps("rorkult/*", "rorkult/0") is True

    def test_prefix_single_star_does_not_match_deeper(self):
        # `prefix/*` should not match `prefix/x/y` — only single trailing segment.
        assert _source_id_overlaps("rorkult/*", "rorkult/0/setpoint") is False


# --------------------------------------------------------------------------
# _is_loopback
# --------------------------------------------------------------------------


class TestIsLoopback:
    _ARGS = dict(
        connector_entity_id="rover-1",
        connector_source_id="rorkult/0",
    )

    def test_different_entity_is_never_loopback(self):
        assert (
            _is_loopback(
                axis_entity_id="other-entity",
                axis_source_id="rorkult/0",
                **self._ARGS,
            )
            is False
        )

    def test_exact_source_match_on_same_entity_is_loopback(self):
        assert (
            _is_loopback(
                axis_entity_id="rover-1",
                axis_source_id="rorkult/0",
                **self._ARGS,
            )
            is True
        )

    def test_setpoint_subnamespace_is_loopback(self):
        assert (
            _is_loopback(
                axis_entity_id="rover-1",
                axis_source_id="rorkult/0/setpoint",
                **self._ARGS,
            )
            is True
        )

    def test_measured_subnamespace_is_loopback(self):
        assert (
            _is_loopback(
                axis_entity_id="rover-1",
                axis_source_id="rorkult/0/measured",
                **self._ARGS,
            )
            is True
        )

    def test_wildcard_on_same_entity_is_loopback(self):
        assert (
            _is_loopback(
                axis_entity_id="rover-1",
                axis_source_id="**",
                **self._ARGS,
            )
            is True
        )

    def test_unrelated_source_on_same_entity_is_not_loopback(self):
        assert (
            _is_loopback(
                axis_entity_id="rover-1",
                axis_source_id="joystick/1",
                **self._ARGS,
            )
            is False
        )


# --------------------------------------------------------------------------
# ControlAxisState
# --------------------------------------------------------------------------


def _enclose_float(value: float) -> bytes:
    """Build a Keelson envelope wrapping a TimestampedFloat."""
    payload = TimestampedFloat()
    payload.timestamp.GetCurrentTime()
    payload.value = value
    return keelson.enclose(payload.SerializeToString())


def _mock_sample(envelope_bytes: bytes):
    """Build a mock Zenoh sample whose .payload.to_bytes() returns the
    given envelope bytes."""
    sample = MagicMock()
    sample.payload.to_bytes = MagicMock(return_value=envelope_bytes)
    return sample


class _FakeSession:
    """Captures declare_subscriber calls so tests can trigger samples."""

    def __init__(self):
        self.subscribers: dict[str, MagicMock] = {}
        self.callbacks: dict[str, callable] = {}

    def declare_subscriber(self, key, callback):
        sub = MagicMock()
        sub.undeclare = MagicMock()
        sub.key = key
        self.subscribers[key] = sub
        self.callbacks[key] = callback
        return sub


@pytest.fixture
def session():
    return _FakeSession()


@pytest.fixture
def state(session):
    return ControlAxisState(
        session=session,
        connector_realm="test",
        connector_entity_id="rover-1",
        connector_source_id="rorkult/0",
    )


def _mapping(
    *,
    steering_subject: str = "joystick_x_pct",
    steering_source: str = "gamepad-1",
    throttle_subject: str = "joystick_y_pct",
    throttle_source: str = "gamepad-1",
    min_interval_s: float = 0.0,
    max_axis_age_s: float = 0.0,
    steering_entity: str = "",
    throttle_entity: str = "",
    steering_unipolar: bool = False,
    steering_invert: bool = False,
) -> ControlAxisMapping:
    return ControlAxisMapping(
        axes={
            "steering": ControlAxis(
                entity_id=steering_entity,
                subject=steering_subject,
                source_id=steering_source,
                unipolar=steering_unipolar,
                invert=steering_invert,
            ),
            "throttle": ControlAxis(
                entity_id=throttle_entity,
                subject=throttle_subject,
                source_id=throttle_source,
            ),
        },
        min_interval_s=min_interval_s,
        max_axis_age_s=max_axis_age_s,
    )


class TestSetMapping:
    def test_valid_mapping_installs_subscribers(self, state, session):
        state.set_mapping(_mapping())
        assert len(session.subscribers) == 2
        assert any("joystick_x_pct" in k for k in session.subscribers)
        assert any("joystick_y_pct" in k for k in session.subscribers)

    def test_unknown_axis_rejected(self, state):
        bad = ControlAxisMapping(
            axes={"wibble": ControlAxis(subject="joystick_x_pct", source_id="x")}
        )
        with pytest.raises(ValueError, match="unknown axis"):
            state.set_mapping(bad)

    def test_empty_subject_rejected(self, state):
        bad = ControlAxisMapping(
            axes={"steering": ControlAxis(subject="", source_id="x")}
        )
        with pytest.raises(ValueError, match="empty subject"):
            state.set_mapping(bad)

    def test_loopback_rejected(self, state):
        # source_id matching the connector's own
        bad = ControlAxisMapping(
            axes={
                "steering": ControlAxis(
                    subject="joystick_x_pct",
                    source_id="rorkult/0",
                    entity_id="rover-1",
                )
            }
        )
        with pytest.raises(LoopbackError, match="overlaps"):
            state.set_mapping(bad)

    def test_loopback_via_setpoint_subnamespace(self, state):
        bad = ControlAxisMapping(
            axes={
                "steering": ControlAxis(
                    subject="joystick_x_pct",
                    source_id="rorkult/0/setpoint",
                    entity_id="rover-1",
                )
            }
        )
        with pytest.raises(LoopbackError):
            state.set_mapping(bad)

    def test_loopback_via_wildcard_on_own_entity(self, state):
        bad = ControlAxisMapping(
            axes={
                "steering": ControlAxis(
                    subject="joystick_x_pct",
                    source_id="**",
                    entity_id="rover-1",
                )
            }
        )
        with pytest.raises(LoopbackError):
            state.set_mapping(bad)

    def test_invalid_mapping_leaves_old_intact(self, state, session):
        # Install a good mapping, then try a bad one — the bad one should
        # be rejected before any subscriber is touched.
        state.set_mapping(_mapping())
        original_subs = dict(session.subscribers)
        bad = ControlAxisMapping(
            axes={"wibble": ControlAxis(subject="x", source_id="y")}
        )
        with pytest.raises(ValueError):
            state.set_mapping(bad)
        assert session.subscribers == original_subs
        # Old subscribers should not have been undeclared.
        for sub in original_subs.values():
            sub.undeclare.assert_not_called()

    def test_replace_undeclares_old_subscribers(self, state, session):
        state.set_mapping(_mapping(steering_source="gamepad-1"))
        old_subs = list(session.subscribers.values())

        state.set_mapping(_mapping(steering_source="gamepad-2"))
        for sub in old_subs:
            sub.undeclare.assert_called_once()

    def test_empty_mapping_clears_axes(self, state, session):
        state.set_mapping(_mapping())
        state.set_mapping(ControlAxisMapping())
        # Mapping is empty; get_mapping returns no axes.
        assert len(state.get_mapping().axes) == 0


class TestGetMapping:
    def test_returns_what_was_set(self, state):
        state.set_mapping(_mapping(min_interval_s=0.05))
        got = state.get_mapping()
        assert set(got.axes.keys()) == {"steering", "throttle"}
        # proto3 float is single-precision; tolerate the round-trip drift.
        assert got.min_interval_s == pytest.approx(0.05)

    def test_substitutes_default_dead_man_when_unset(self, state):
        state.set_mapping(_mapping(max_axis_age_s=0.0))
        got = state.get_mapping()
        assert got.max_axis_age_s == _DEFAULT_MAX_AXIS_AGE_S

    def test_preserves_explicit_dead_man(self, state):
        state.set_mapping(_mapping(max_axis_age_s=0.25))
        assert state.get_mapping().max_axis_age_s == 0.25


class TestEmissionLifecycle:
    def _trigger(self, session, subject_substr: str, value: float) -> None:
        key = next(k for k in session.callbacks if subject_substr in k)
        session.callbacks[key](_mock_sample(_enclose_float(value)))

    def test_engages_only_after_all_axes_have_published(
        self, state, session, caplog
    ):
        caplog.set_level(logging.INFO, logger="rorkult.control_axis")
        state.set_mapping(_mapping())

        # Only steering — throttle hasn't published yet; emission stays
        # disengaged (dead-man fails closed).
        self._trigger(session, "joystick_x_pct", 50.0)
        assert not any("control engaged" in r.message for r in caplog.records)

        # Now throttle arrives — emission engages.
        self._trigger(session, "joystick_y_pct", -25.0)
        assert any("control engaged" in r.message for r in caplog.records)

    def test_disengages_when_axis_goes_stale(self, state, session, caplog):
        caplog.set_level(logging.INFO, logger="rorkult.control_axis")
        # Tight dead-man so the test doesn't have to wait long.
        state.set_mapping(_mapping(max_axis_age_s=0.05))

        self._trigger(session, "joystick_x_pct", 10.0)
        self._trigger(session, "joystick_y_pct", 10.0)
        assert any("control engaged" in r.message for r in caplog.records)

        # Wait past the dead-man window, then send a sample on just one
        # axis. The other axis is now stale -> disengage.
        time.sleep(0.1)
        self._trigger(session, "joystick_x_pct", 10.0)
        assert any("control disengaged" in r.message for r in caplog.records)

    def test_min_interval_gates_emission(self, state, session, caplog):
        caplog.set_level(logging.DEBUG, logger="rorkult.control_axis")
        state.set_mapping(_mapping(min_interval_s=10.0))  # effectively forever

        self._trigger(session, "joystick_x_pct", 50.0)
        self._trigger(session, "joystick_y_pct", -50.0)
        # First arrival after the throttle sample would normally engage,
        # but the throttle check happens *after* the engage decision.
        # What we can verify is that no emit-debug log appears beyond
        # the very first one. Simplest test: send many more samples and
        # verify the "would forward" log only appears once (or zero
        # times if the gate caught the very first emit too).
        for _ in range(5):
            self._trigger(session, "joystick_x_pct", 50.0)
            self._trigger(session, "joystick_y_pct", -50.0)
        emit_logs = [r for r in caplog.records if "would forward" in r.message]
        assert len(emit_logs) <= 1, (
            f"min_interval gate should suppress emits, but got {len(emit_logs)}"
        )

    def test_malformed_envelope_does_not_crash(self, state, session, caplog):
        caplog.set_level(logging.ERROR, logger="rorkult.control_axis")
        state.set_mapping(_mapping())
        key = next(k for k in session.callbacks if "joystick_x_pct" in k)
        bad_sample = MagicMock()
        bad_sample.payload.to_bytes = MagicMock(return_value=b"\xff\xff\xffnotanenvelope")
        # Should not raise; the handler logs and returns.
        session.callbacks[key](bad_sample)


class TestClose:
    def test_undeclares_all_subscribers(self, state, session):
        state.set_mapping(_mapping())
        subs = list(session.subscribers.values())
        state.close()
        for sub in subs:
            sub.undeclare.assert_called_once()

    def test_idempotent(self, state):
        state.close()
        state.close()  # second call on empty state is fine
