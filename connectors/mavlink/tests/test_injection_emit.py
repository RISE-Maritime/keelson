"""Unit tests for the skarv-based GPS_INPUT injection emit + trigger flow.

The tests put pre-built Envelopes into skarv's vault under the relevant
keelson subject names, call _emit_gps_input directly, and assert on the
MAVLink wire call.
"""

import argparse
import time
from unittest.mock import MagicMock

import pytest
import skarv

from conftest import injection_config as ic
from conftest import mavlink2keelson as m2k

from keelson import enclose
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ts(message):
    """Set a google.protobuf.Timestamp on a freshly-built message to now."""
    message.timestamp.GetCurrentTime()
    return message


def _wrap_zenoh_bytes(payload_bytes: bytes):
    """Wrap raw bytes in a mock Zenoh payload with `.to_bytes()`.

    Several other connectors (ais, nmea) register skarv triggers at
    module-load that expect this shape and call `sample.value.to_bytes()`
    on every put — so even our mavlink-only test fixtures must produce
    payloads in the same shape to avoid cross-connector trigger crashes.
    """
    p = MagicMock()
    p.to_bytes = MagicMock(return_value=payload_bytes)
    return p


def _put_location_fix(lat=59.0, lon=18.0, alt=5.0, stamp_now=True):
    fix = LocationFix(latitude=lat, longitude=lon, altitude=alt)
    if stamp_now:
        fix.timestamp.GetCurrentTime()
    skarv.put("location_fix", _wrap_zenoh_bytes(enclose(fix.SerializeToString())))


def _put_tint(subject: str, value: int, stamp_now=True):
    msg = TimestampedInt(value=value)
    if stamp_now:
        msg.timestamp.GetCurrentTime()
    skarv.put(subject, _wrap_zenoh_bytes(enclose(msg.SerializeToString())))


def _put_tfloat(subject: str, value: float, stamp_now=True):
    msg = TimestampedFloat(value=value)
    if stamp_now:
        msg.timestamp.GetCurrentTime()
    skarv.put(subject, _wrap_zenoh_bytes(enclose(msg.SerializeToString())))


def _put_tfloat_stale(subject: str, value: float, age_s: float):
    msg = TimestampedFloat(value=value)
    past = time.time() - age_s
    msg.timestamp.seconds = int(past)
    msg.timestamp.nanos = int((past - int(past)) * 1e9)
    skarv.put(subject, _wrap_zenoh_bytes(enclose(msg.SerializeToString())))


def _make_mapping(throttle_s=None, max_companion_age_s=None):
    """Build a GPS_INPUT InjectionMapping with all known sources declared
    (so the emit function can fetch any companion that's been put)."""
    spec = ic.MESSAGE_REGISTRY["GPS_INPUT"]
    all_subjects = (
        spec.trigger_subject,
        *spec.required_companions,
        *spec.optional_companions,
    )
    sources = [
        ic.SourceSpec(subject=s, entity_id="motorboat-01", source_id="external-gnss/0")
        for s in all_subjects
    ]
    return ic.InjectionMapping(
        spec=spec,
        sources=sources,
        throttle_s=throttle_s,
        max_companion_age_s=max_companion_age_s,
    )


def _args():
    return argparse.Namespace(target_system=1, target_component=0)


def _mock_mav():
    mav = MagicMock()
    mav.mav = MagicMock()
    return mav


# ---------------------------------------------------------------------------
# Emit: happy path
# ---------------------------------------------------------------------------


class TestEmitHappyPath:
    def test_emits_when_trigger_present(self):
        _put_location_fix(lat=59.351, lon=18.071, alt=10.0)
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 12)

        mav = _mock_mav()
        emitted = m2k._emit_gps_input(mav, _args(), _make_mapping())

        assert emitted is True
        assert mav.mav.gps_input_send.called
        call = mav.mav.gps_input_send.call_args.args
        # gps_id (arg 1) = 0 (primary)
        assert call[1] == 0
        # fix_type (arg 5)
        assert call[5] == 3
        # lat / lon in degE7 (args 6, 7)
        assert call[6] == int(59.351 * 1e7)
        assert call[7] == int(18.071 * 1e7)
        # alt MSL (arg 8)
        assert call[8] == pytest.approx(10.0)
        # satellites_visible (arg 17)
        assert call[17] == 12

    def test_missing_trigger_skips_emission(self):
        # Nothing in the vault yet.
        mav = _mock_mav()
        emitted = m2k._emit_gps_input(mav, _args(), _make_mapping())
        assert emitted is False
        assert not mav.mav.gps_input_send.called

    def test_defaults_applied_when_required_companions_missing(self):
        _put_location_fix()
        # Don't put gps_fix_type or satellites_visible.

        mav = _mock_mav()
        emitted = m2k._emit_gps_input(mav, _args(), _make_mapping())

        assert emitted is True
        call = mav.mav.gps_input_send.call_args.args
        assert call[5] == 3  # fix_type default
        assert call[17] == 6  # satellites_visible default


# ---------------------------------------------------------------------------
# Emit: ignore-bit accounting
# ---------------------------------------------------------------------------


class TestIgnoreBits:
    def test_missing_hdop_vdop_sets_ignore_bits(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)

        mav = _mock_mav()
        m2k._emit_gps_input(mav, _args(), _make_mapping())

        ignore = mav.mav.gps_input_send.call_args.args[2]
        assert ignore & m2k._GPS_IGN_HDOP
        assert ignore & m2k._GPS_IGN_VDOP
        assert ignore & m2k._GPS_IGN_HACC
        assert ignore & m2k._GPS_IGN_VACC
        assert ignore & m2k._GPS_IGN_VEL_H
        assert ignore & m2k._GPS_IGN_VEL_V
        # speed_accuracy is always ignored in v1 (no companion)
        assert ignore & m2k._GPS_IGN_SPEED_ACC

    def test_hdop_present_clears_hdop_ignore(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_tfloat("location_fix_hdop", 0.7)

        mav = _mock_mav()
        m2k._emit_gps_input(mav, _args(), _make_mapping())

        call = mav.mav.gps_input_send.call_args.args
        ignore = call[2]
        assert not (ignore & m2k._GPS_IGN_HDOP)
        # HDOP value lands at arg 9
        assert call[9] == pytest.approx(0.7)

    def test_climb_rate_present_clears_velocity_down_ignore(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_tfloat("climb_rate_mps", 0.5)

        mav = _mock_mav()
        m2k._emit_gps_input(mav, _args(), _make_mapping())

        call = mav.mav.gps_input_send.call_args.args
        ignore = call[2]
        assert not (ignore & m2k._GPS_IGN_VEL_V)
        # vd is -climb (positive-down convention); arg 13
        assert call[13] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# Velocity decomposition (SOG + COG -> vN / vE)
# ---------------------------------------------------------------------------


class TestVelocityDecomposition:
    def test_due_north_course_maps_to_vn_only(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        # 10 knots due north (cog = 0 degrees)
        _put_tfloat("speed_over_ground_knots", 10.0)
        _put_tfloat("course_over_ground_deg", 0.0)

        mav = _mock_mav()
        m2k._emit_gps_input(mav, _args(), _make_mapping())

        call = mav.mav.gps_input_send.call_args.args
        # vn / ve at args 11, 12; 10 kn ≈ 5.144 m/s
        assert call[11] == pytest.approx(5.144444, abs=0.001)
        assert call[12] == pytest.approx(0.0, abs=0.001)
        # vel-h ignore cleared
        assert not (call[2] & m2k._GPS_IGN_VEL_H)

    def test_due_east_course_maps_to_ve_only(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_tfloat("speed_over_ground_knots", 10.0)
        _put_tfloat("course_over_ground_deg", 90.0)

        mav = _mock_mav()
        m2k._emit_gps_input(mav, _args(), _make_mapping())

        call = mav.mav.gps_input_send.call_args.args
        assert call[11] == pytest.approx(0.0, abs=0.001)
        assert call[12] == pytest.approx(5.144444, abs=0.001)

    def test_sog_without_cog_falls_back_to_ignore_bit(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_tfloat("speed_over_ground_knots", 10.0)
        # COG not published.

        mav = _mock_mav()
        m2k._emit_gps_input(mav, _args(), _make_mapping())

        call = mav.mav.gps_input_send.call_args.args
        assert call[2] & m2k._GPS_IGN_VEL_H


# ---------------------------------------------------------------------------
# Staleness guard
# ---------------------------------------------------------------------------


class TestStalenessGuard:
    def test_stale_companion_skips_emission(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        # HDOP is 10 s stale; limit is 1 s.
        _put_tfloat_stale("location_fix_hdop", 0.7, age_s=10.0)

        mav = _mock_mav()
        emitted = m2k._emit_gps_input(
            mav,
            _args(),
            _make_mapping(max_companion_age_s=1.0),
        )

        assert emitted is False
        assert not mav.mav.gps_input_send.called

    def test_fresh_companion_passes_age_check(self):
        _put_location_fix()
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_tfloat("location_fix_hdop", 0.7)

        mav = _mock_mav()
        emitted = m2k._emit_gps_input(
            mav,
            _args(),
            _make_mapping(max_companion_age_s=1.0),
        )

        assert emitted is True


# ---------------------------------------------------------------------------
# Throttle / trigger wiring via _install_injection_mappings
# ---------------------------------------------------------------------------


class TestThrottleAndTrigger:
    def test_throttle_skips_close_emissions(self, monkeypatch):
        """Drive the full trigger path: install the mapping (registers a
        skarv.trigger), then put location_fix twice in quick succession.
        Throttle should drop the second emission."""
        mapping = _make_mapping(throttle_s=10.0)
        rate_monitor = m2k.RateMonitor(limits={"location_fix": (1.0, 20.0)})
        mav = _mock_mav()

        # _install_injection_mappings needs a session.declare_subscriber that
        # doesn't crash. The skarv_mirror it calls is the function in
        # skarv.utilities.zenoh; we patch it out so no real subscribers
        # are declared, but we still keep the skarv.trigger registration.
        monkeypatch.setattr(m2k, "skarv_mirror", lambda *a, **kw: None)

        session = MagicMock()
        args = argparse.Namespace(
            realm="test",
            entity_id="motorboat-01",
            source_id="mav/0",
            target_system=1,
            target_component=0,
        )

        m2k._install_injection_mappings(session, args, mav, [mapping], rate_monitor)

        # First put -> fires trigger -> emits
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_location_fix()
        first_calls = mav.mav.gps_input_send.call_count
        assert first_calls >= 1

        # Second put within the throttle window -> no new emission
        _put_location_fix()
        assert mav.mav.gps_input_send.call_count == first_calls

    def test_trigger_records_rate(self, monkeypatch):
        mapping = _make_mapping()
        rate_monitor = m2k.RateMonitor(limits={"location_fix": (1.0, 20.0)})
        mav = _mock_mav()
        monkeypatch.setattr(m2k, "skarv_mirror", lambda *a, **kw: None)
        session = MagicMock()
        args = argparse.Namespace(
            realm="test",
            entity_id="motorboat-01",
            source_id="mav/0",
            target_system=1,
            target_component=0,
        )

        m2k._install_injection_mappings(session, args, mav, [mapping], rate_monitor)
        _put_tint("gps_fix_type", 3)
        _put_tint("location_fix_satellites_visible", 8)
        _put_location_fix()

        # Rate monitor should have seen at least one location_fix arrival.
        assert "location_fix" in rate_monitor._first_sample_at
        assert len(rate_monitor._arrivals["location_fix"]) >= 1


# ---------------------------------------------------------------------------
# --strict-rates: floor / silence transitions raise RuntimeError so CI can
# gate deployments on healthy injection rates without leaving prod
# connectors fragile to a single network blip.
# ---------------------------------------------------------------------------


class TestRateMonitorStrictMode:
    SUBJECT = "location_fix"
    LIMITS = {SUBJECT: (5.0, 20.0)}

    def _advance(self, monkeypatch, seconds):
        """Push time.time() forward by `seconds` so the RateMonitor sees a
        rolling-window state transition without us actually sleeping."""
        base = m2k.time.time()
        offset = [seconds]
        monkeypatch.setattr(m2k.time, "time", lambda: base + offset[0])

    def test_strict_raises_on_floor_violation(self, monkeypatch):
        rm = m2k.RateMonitor(limits=self.LIMITS, strict=True)
        # One arrival, far below the 5 Hz floor.
        rm.record(self.SUBJECT)
        # Jump past MIN_OBSERVATION_S so the monitor commits to a decision.
        self._advance(monkeypatch, m2k.RateMonitor.MIN_OBSERVATION_S + 0.5)
        with pytest.raises(RuntimeError, match="below floor"):
            rm.check()

    def test_strict_raises_on_silent_transition(self, monkeypatch):
        rm = m2k.RateMonitor(limits=self.LIMITS, strict=True)
        # One arrival, then a long silence.
        rm.record(self.SUBJECT)
        # silence > SILENT_MULTIPLIER * WINDOW_S → "silent" transition.
        gap = m2k.RateMonitor.SILENT_MULTIPLIER * m2k.RateMonitor.WINDOW_S + 1.0
        self._advance(monkeypatch, gap)
        with pytest.raises(RuntimeError, match="not produced a sample"):
            rm.check()

    def test_forgiving_logs_warning_instead_of_raising(self, monkeypatch, caplog):
        rm = m2k.RateMonitor(limits=self.LIMITS, strict=False)
        rm.record(self.SUBJECT)
        self._advance(monkeypatch, m2k.RateMonitor.MIN_OBSERVATION_S + 0.5)
        # Should not raise even though the rate is far below the floor.
        with caplog.at_level("WARNING"):
            rm.check()
        assert any("below floor" in r.message for r in caplog.records)

    def test_no_transition_no_action(self, monkeypatch):
        # 60 arrivals over a ~5 s window → 12 Hz, comfortably inside the
        # (5, 20) Hz band. Strict mode must not raise on the ok state.
        rm = m2k.RateMonitor(limits=self.LIMITS, strict=True)
        for _ in range(60):
            rm.record(self.SUBJECT)
        self._advance(monkeypatch, m2k.RateMonitor.MIN_OBSERVATION_S + 0.5)
        rm.check()  # must not raise — we're inside the band
        # The default state is "ok"; transitions only flip _state when they
        # diverge from the default, so an empty dict here is intentional.
        assert rm._state.get(self.SUBJECT, "ok") == "ok"

    def test_strict_does_not_raise_below_min_observation(self, monkeypatch):
        # Don't commit to a state transition before MIN_OBSERVATION_S — early
        # samples are too sparse to be a reliable signal.
        rm = m2k.RateMonitor(limits=self.LIMITS, strict=True)
        rm.record(self.SUBJECT)
        self._advance(monkeypatch, m2k.RateMonitor.MIN_OBSERVATION_S - 0.5)
        rm.check()  # too early to decide → no raise
        assert self.SUBJECT not in rm._state
