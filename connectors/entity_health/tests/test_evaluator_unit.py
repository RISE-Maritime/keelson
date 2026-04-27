"""Unit tests for the pure evaluator logic (no Zenoh)."""

from types import SimpleNamespace

from entity_health.evaluator import (
    Band,
    CheckResult,
    ContentRule,
    Evaluator,
    Expectation,
    SourceState,
    evaluate_all,
    parse_level,
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_INACTIVE,
    HEALTH_NOMINAL,
    HEALTH_UNKNOWN,
)


def _publication_rate_hz_for(expected: float, tol_pct: float = 20.0) -> list[Band]:
    """Nominal within ±tol, DEGRADED within ±2×tol, otherwise CRITICAL."""
    tol = expected * (tol_pct / 100.0)
    return [
        Band(level=HEALTH_NOMINAL, min=expected - tol, max=expected + tol),
        Band(level=HEALTH_DEGRADED, min=expected - 2 * tol, max=expected + 2 * tol),
    ]


def _make(**kwargs) -> Evaluator:
    defaults = dict(
        name="x",
        key_expr="k/**",
        inactive_after_s=2.0,
        window_s=2.0,
        publication_rate_hz=_publication_rate_hz_for(10.0, 20.0),
        require_liveliness=False,
    )
    defaults.update(kwargs)
    return Evaluator(Expectation(**defaults))


def test_no_samples_without_liveliness_required_is_inactive():
    ev = _make()
    assert ev.evaluate(now=100.0).level == HEALTH_INACTIVE


def test_no_liveliness_token_is_unknown():
    """Liveliness failure is conveyed by source.level=UNKNOWN with empty checks."""
    ev = _make(require_liveliness=True)
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_UNKNOWN
    assert state.checks == []


def test_liveliness_present_no_samples_is_inactive():
    ev = _make(require_liveliness=True)
    ev.set_alive("k/a")
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_INACTIVE
    activity = next(c for c in state.checks if c.name == "activity")
    assert activity.level == HEALTH_INACTIVE
    assert "alive but no samples" in activity.detail


def test_liveliness_present_with_data_is_nominal():
    ev = _make(require_liveliness=True)
    ev.set_alive("k/a")
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL


def test_liveliness_removed_goes_back_to_unknown():
    ev = _make(require_liveliness=True)
    ev.set_alive("k/a")
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL
    ev.set_dead("k/a")
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_UNKNOWN


def test_liveliness_tracks_multiple_sources():
    ev = _make(require_liveliness=True)
    ev.set_alive("k/a")
    ev.set_alive("k/b")
    ev.set_dead("k/a")
    assert ev.is_alive
    ev.set_dead("k/b")
    assert not ev.is_alive


def test_rate_within_tolerance_is_nominal():
    ev = _make()
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)  # 10 Hz
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL


def test_silence_beyond_inactive_after_is_inactive():
    ev = _make(inactive_after_s=2.0)
    ev.record(now=100.0)
    state = ev.evaluate(now=105.0)
    assert state.level == HEALTH_INACTIVE
    activity = next(c for c in state.checks if c.name == "activity")
    assert activity.level == HEALTH_INACTIVE
    assert "silent" in activity.detail
    assert "limit 2.0s" in activity.detail


def test_rate_in_degraded_band():
    ev = _make(publication_rate_hz=_publication_rate_hz_for(10.0, 20.0))
    # 5 Hz — outside NOMINAL [8,12] but inside DEGRADED [6,14]... wait, recompute
    # tol=2 → NOMINAL [8,12], DEGRADED [6,14]. Use 7 Hz to land in DEGRADED.
    for i in range(14):
        ev.record(now=1000.0 + i / 7.0)
    level = ev.evaluate(now=1000.0 + 2.0).level
    assert level == HEALTH_DEGRADED


def test_rate_outside_all_bands_uses_default_level():
    ev = _make(publication_rate_hz=_publication_rate_hz_for(10.0, 20.0))
    # 2 Hz — outside both NOMINAL and DEGRADED bands → default CRITICAL
    for i in range(4):
        ev.record(now=1000.0 + i * 0.5)
    level = ev.evaluate(now=1000.0 + 2.0).level
    assert level == HEALTH_CRITICAL


def _lat_rule() -> ContentRule:
    return ContentRule(
        field="latitude",
        bands=[Band(level=HEALTH_NOMINAL, min=-90, max=90)],
        default_level=HEALTH_CRITICAL,
    )


def test_content_rule_out_of_range_uses_default_level():
    ev = _make(content_rules=[_lat_rule()])
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1, payload=SimpleNamespace(latitude=200.0))
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_CRITICAL
    lat = next(c for c in state.checks if c.name == "latitude")
    assert "latitude" in lat.detail


def test_content_rule_in_range_is_nominal():
    ev = _make(content_rules=[_lat_rule()])
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1, payload=SimpleNamespace(latitude=45.0))
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL


def test_evaluate_all_aggregates_worst():
    good = _make(name="good")
    bad = _make(name="bad", inactive_after_s=1.0)
    for i in range(20):
        good.record(now=1000.0 + i * 0.1)
    bad.record(now=1000.0)
    overall, states = evaluate_all([good, bad], now=1005.0)
    assert overall == HEALTH_INACTIVE
    assert {s.name for s in states} == {"good", "bad"}


def test_evaluate_all_empty_is_unknown():
    overall, states = evaluate_all([], now=100.0)
    assert overall == HEALTH_UNKNOWN
    assert states == []


# --- Tiered band tests ----------------------------------------------------


def _band_eval(value, **rule_kwargs):
    rule = ContentRule(field="value", **rule_kwargs)
    return rule.evaluate(SimpleNamespace(value=value))[0]


def test_band_nominal_match():
    bands = [
        Band(level=HEALTH_NOMINAL, min=12, max=14.5),
        Band(level=HEALTH_DEGRADED, min=11, max=15),
        Band(level=HEALTH_CRITICAL, min=10, max=16),
    ]
    assert _band_eval(13.0, bands=bands) == HEALTH_NOMINAL


def test_band_degraded_match():
    bands = [
        Band(level=HEALTH_NOMINAL, min=12, max=14.5),
        Band(level=HEALTH_DEGRADED, min=11, max=15),
        Band(level=HEALTH_CRITICAL, min=10, max=16),
    ]
    assert _band_eval(11.5, bands=bands) == HEALTH_DEGRADED


def test_band_critical_match():
    bands = [
        Band(level=HEALTH_NOMINAL, min=12, max=14.5),
        Band(level=HEALTH_DEGRADED, min=11, max=15),
        Band(level=HEALTH_CRITICAL, min=10, max=16),
    ]
    assert _band_eval(15.5, bands=bands) == HEALTH_CRITICAL


def test_band_no_match_uses_default_level():
    bands = [Band(level=HEALTH_NOMINAL, min=12, max=14.5)]
    assert (
        _band_eval(20.0, bands=bands, default_level=HEALTH_CRITICAL) == HEALTH_CRITICAL
    )


def test_evaluator_combines_rate_and_tiered_content_worst_wins():
    bands = [
        Band(level=HEALTH_NOMINAL, min=12, max=14.5),
        Band(level=HEALTH_CRITICAL, min=10, max=16),
    ]
    exp = Expectation(
        name="batt",
        key_expr="k/**",
        inactive_after_s=5.0,
        window_s=2.0,
        publication_rate_hz=_publication_rate_hz_for(10.0, 20.0),
        content_rules=[ContentRule(field="value", bands=bands)],
        require_liveliness=False,
    )
    ev = Evaluator(exp)
    # Healthy rate, but value lands in CRITICAL band
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1, payload=SimpleNamespace(value=15.5))
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_CRITICAL
    value_check = next(c for c in state.checks if c.name == "value")
    assert "value=15.5" in value_check.detail


def test_band_equals_string_match():
    bands = [
        Band(level=HEALTH_CRITICAL, equals="foo"),
        Band(level=HEALTH_NOMINAL, equals=["bar", "baz"]),
    ]
    rule = ContentRule(field="value", bands=bands, default_level=HEALTH_DEGRADED)
    assert rule.evaluate(SimpleNamespace(value="foo"))[0] == HEALTH_CRITICAL
    assert rule.evaluate(SimpleNamespace(value="bar"))[0] == HEALTH_NOMINAL
    assert rule.evaluate(SimpleNamespace(value="baz"))[0] == HEALTH_NOMINAL
    assert rule.evaluate(SimpleNamespace(value="other"))[0] == HEALTH_DEGRADED


def test_band_equals_bool_match():
    bands = [Band(level=HEALTH_CRITICAL, equals=False)]
    rule = ContentRule(field="value", bands=bands, default_level=HEALTH_NOMINAL)
    assert rule.evaluate(SimpleNamespace(value=False))[0] == HEALTH_CRITICAL
    assert rule.evaluate(SimpleNamespace(value=True))[0] == HEALTH_NOMINAL


def test_protobuf_enum_matched_by_name():
    """ContentRule should match enum fields by symbolic name via the descriptor."""
    from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality

    msg = LocationFixQuality()
    msg.fix_type = LocationFixQuality.FIX_3D_RTK  # int 9

    rule = ContentRule(
        field="fix_type",
        bands=[
            Band(level=HEALTH_NOMINAL, equals=["FIX_3D_RTK", "FIX_3D"]),
            Band(level=HEALTH_DEGRADED, equals=["FIX_3D_DGPS"]),
        ],
        default_level=HEALTH_CRITICAL,
    )
    assert rule.evaluate(msg)[0] == HEALTH_NOMINAL

    msg.fix_type = LocationFixQuality.INVALID
    assert rule.evaluate(msg)[0] == HEALTH_CRITICAL


def test_protobuf_enum_still_matches_by_int():
    from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality

    msg = LocationFixQuality()
    msg.fix_type = LocationFixQuality.FIX_3D_RTK
    rule = ContentRule(
        field="fix_type",
        bands=[Band(level=HEALTH_NOMINAL, equals=[9])],
        default_level=HEALTH_CRITICAL,
    )
    assert rule.evaluate(msg)[0] == HEALTH_NOMINAL


def test_parse_level_accepts_strings_and_ints():
    assert parse_level("NOMINAL") == HEALTH_NOMINAL
    assert parse_level("HEALTH_DEGRADED") == HEALTH_DEGRADED
    assert parse_level(HEALTH_CRITICAL) == HEALTH_CRITICAL


# --- measured_publication_rate_hz on SubsystemState ----------------------


def test_measured_rate_is_zero_when_no_samples():
    ev = _make()
    assert ev.evaluate(now=100.0).measured_publication_rate_hz == 0.0


def test_measured_rate_reflects_observed_rate_when_nominal():
    ev = _make()  # window_s=2.0
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)  # 10 Hz over 2s window → 10.0 Hz
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_NOMINAL
    assert state.measured_publication_rate_hz == 10.0


def test_measured_rate_populated_when_unknown():
    ev = _make(require_liveliness=True)
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_UNKNOWN
    assert state.measured_publication_rate_hz == 0.0


def test_measured_rate_populated_when_inactive():
    ev = _make(inactive_after_s=2.0)  # window_s=2.0
    ev.record(now=100.0)
    state = ev.evaluate(now=105.0)  # 5s of silence > 2s limit, also outside window
    assert state.level == HEALTH_INACTIVE
    assert state.measured_publication_rate_hz == 0.0


def test_measured_rate_uses_sliding_window():
    ev = _make(inactive_after_s=10.0)  # window_s=2.0
    # Old samples that should be evicted from the rate window
    for i in range(10):
        ev.record(now=1000.0 + i * 0.1)
    # Recent samples inside the 2s window: 4 samples → 2.0 Hz
    for i in range(4):
        ev.record(now=1004.0 + i * 0.1)
    state = ev.evaluate(now=1004.5)
    assert state.measured_publication_rate_hz == 2.0


# --- proto schema -------------------------------------------------------


def test_protobuf_source_health_has_checks_field():
    """SourceHealth proto must expose a repeated CheckResult `checks` field."""
    from keelson.payloads.EntityHealth_pb2 import (
        CheckResult as ProtoCheckResult,
        SourceHealth,
        HEALTH_NOMINAL as PROTO_NOMINAL,
    )

    sh = SourceHealth()
    cr = sh.checks.add()
    cr.name = "publication_rate"
    cr.level = PROTO_NOMINAL
    cr.detail = "ok"
    assert sh.checks[0].name == "publication_rate"
    assert sh.checks[0].level == PROTO_NOMINAL
    assert isinstance(sh.checks[0], ProtoCheckResult)


# --- gate semantics: liveliness vs activity -----------------------------


def test_unknown_gate_emits_no_checks():
    """Liveliness failure: source.level=UNKNOWN, checks empty, no detail elsewhere."""
    ev = _make(require_liveliness=True)
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_UNKNOWN
    assert state.checks == []


def test_inactive_no_samples_emits_only_activity_check():
    """Activity gate failure: only the activity check is emitted."""
    ev = _make(require_liveliness=True)
    ev.set_alive("k/a")
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_INACTIVE
    assert [c.name for c in state.checks] == ["activity"]
    assert state.checks[0].level == HEALTH_INACTIVE
    assert state.checks[0].detail == "alive but no samples received yet"


def test_inactive_silent_emits_only_activity_check():
    ev = _make(inactive_after_s=2.0)
    ev.record(now=100.0)
    state = ev.evaluate(now=105.0)
    assert state.level == HEALTH_INACTIVE
    assert [c.name for c in state.checks] == ["activity"]
    assert state.checks[0].level == HEALTH_INACTIVE
    assert state.checks[0].detail.startswith("silent for ")
    assert "limit 2.0s" in state.checks[0].detail


def test_full_eval_includes_activity_as_nominal():
    """When activity gate passes, activity is still in checks at NOMINAL."""
    ev = _make()
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_NOMINAL
    activity = next(c for c in state.checks if c.name == "activity")
    assert activity.level == HEALTH_NOMINAL
    assert activity.detail == ""


# --- checks[] population on full-eval path ------------------------------


def _check_by_name(checks: list[CheckResult], name: str) -> CheckResult | None:
    return next((c for c in checks if c.name == name), None)


def test_checks_contains_activity_and_publication_rate_when_no_content_rules():
    ev = _make()
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert [c.name for c in state.checks] == ["activity", "publication_rate"]
    assert all(c.level == HEALTH_NOMINAL for c in state.checks)


def test_checks_includes_one_entry_per_content_rule_named_after_field():
    """gnss-style expectation → 4 checks (activity + rate + latitude + longitude), all NOMINAL."""
    lat_rule = ContentRule(
        field="latitude",
        bands=[Band(level=HEALTH_NOMINAL, min=-90, max=90)],
        default_level=HEALTH_CRITICAL,
    )
    lon_rule = ContentRule(
        field="longitude",
        bands=[Band(level=HEALTH_NOMINAL, min=-180, max=180)],
        default_level=HEALTH_CRITICAL,
    )
    ev = _make(content_rules=[lat_rule, lon_rule])
    for i in range(20):
        ev.record(
            now=1000.0 + i * 0.1,
            payload=SimpleNamespace(latitude=45.0, longitude=10.0),
        )
    state = ev.evaluate(now=1000.0 + 2.0)
    assert [c.name for c in state.checks] == [
        "activity",
        "publication_rate",
        "latitude",
        "longitude",
    ]
    assert all(c.level == HEALTH_NOMINAL for c in state.checks)


def test_checks_carry_per_check_levels_and_details_when_mixed():
    """One content rule fails CRITICAL, the other stays NOMINAL → checks[] reflects both."""
    lat_rule = ContentRule(
        field="latitude",
        bands=[Band(level=HEALTH_NOMINAL, min=-90, max=90)],
        default_level=HEALTH_CRITICAL,
    )
    lon_rule = ContentRule(
        field="longitude",
        bands=[Band(level=HEALTH_NOMINAL, min=-180, max=180)],
        default_level=HEALTH_CRITICAL,
    )
    ev = _make(content_rules=[lat_rule, lon_rule])
    for i in range(20):
        ev.record(
            now=1000.0 + i * 0.1,
            payload=SimpleNamespace(latitude=200.0, longitude=10.0),
        )
    state = ev.evaluate(now=1000.0 + 2.0)
    lat = _check_by_name(state.checks, "latitude")
    lon = _check_by_name(state.checks, "longitude")
    rate = _check_by_name(state.checks, "publication_rate")
    assert lat is not None and lat.level == HEALTH_CRITICAL
    assert "latitude" in lat.detail
    assert lon is not None and lon.level == HEALTH_NOMINAL
    assert lon.detail == ""
    assert rate is not None and rate.level == HEALTH_NOMINAL


def test_source_state_has_empty_checks_by_default():
    """SourceState should expose a `checks` list, defaulting to empty."""
    state = SourceState(name="x", level=HEALTH_NOMINAL)
    assert state.checks == []


def test_check_result_is_a_dataclass_with_name_level_detail():
    cr = CheckResult(name="publication_rate", level=HEALTH_NOMINAL)
    assert cr.name == "publication_rate"
    assert cr.level == HEALTH_NOMINAL
    assert cr.detail == ""


def test_measured_rate_populated_when_critical_from_rate_band():
    ev = _make(publication_rate_hz=_publication_rate_hz_for(10.0, 20.0))
    # 2 Hz over 2s window → outside both NOMINAL and DEGRADED bands → CRITICAL
    for i in range(4):
        ev.record(now=1000.0 + i * 0.5)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_CRITICAL
    assert state.measured_publication_rate_hz == 2.0
