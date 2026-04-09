"""Unit tests for the pure evaluator logic (no Zenoh)."""

from types import SimpleNamespace

from entity_health.evaluator import (
    Band,
    ContentRule,
    Evaluator,
    Expectation,
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
    ev = _make(require_liveliness=True)
    assert ev.evaluate(now=100.0).level == HEALTH_UNKNOWN
    assert "liveliness" in ev.evaluate(now=100.0).detail


def test_liveliness_present_no_samples_is_inactive():
    ev = _make(require_liveliness=True)
    ev.set_alive("k/a")
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_INACTIVE
    assert "alive but no samples" in state.detail


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
    assert "silent" in state.detail


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
    assert "latitude" in state.detail


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
    assert "value=15.5" in state.detail


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
