"""Unit tests for the pure evaluator logic (no Zenoh)."""

from types import SimpleNamespace

import pytest

from entity_health.evaluator import (
    Band,
    CheckResult,
    ContentRule,
    Evaluator,
    Expectation,
    SourceLiveliness,
    SourceState,
    SubjectState,
    evaluate_grouped,
    parse_level,
    worst,
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_INACTIVE,
    HEALTH_NOMINAL,
    HEALTH_NOT_ADVERTISED,
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
    """Legacy-shaped presence (source token only, no subject tokens at all)
    falls back to activity-based evaluation — state machine row (d)."""
    ev = _make(require_liveliness=True)
    ev.liveliness.add_source_token("k/a")
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_INACTIVE
    activity = next(c for c in state.checks if c.name == "activity")
    assert activity.level == HEALTH_INACTIVE
    assert "alive but no samples" in activity.detail


def test_liveliness_present_with_data_is_nominal():
    ev = _make(require_liveliness=True)
    ev.liveliness.add_source_token("k/a")
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL


def test_liveliness_removed_goes_back_to_unknown():
    ev = _make(require_liveliness=True)
    ev.liveliness.add_source_token("k/a")
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL
    ev.liveliness.remove_source_token("k/a")
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_UNKNOWN


def test_liveliness_tracks_multiple_sources():
    ev = _make(require_liveliness=True)
    ev.liveliness.add_source_token("k/a")
    ev.liveliness.add_source_token("k/b")
    ev.liveliness.remove_source_token("k/a")
    assert ev.liveliness.is_present
    ev.liveliness.remove_source_token("k/b")
    assert not ev.liveliness.is_present


def test_subject_token_alone_counts_as_presence():
    """A live subject-level token is itself proof the declaring process is
    up (tokens die with the session). Covers producers whose source-level
    token lives under a different source_id than their pubsub keys —
    labjack's per-channel identities — which must evaluate normally, not
    report UNKNOWN."""
    ev = _make(require_liveliness=True)
    ev.liveliness.add_subject("x")  # watched subject advertised, no source token
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    assert ev.evaluate(now=1000.0 + 2.0).level == HEALTH_NOMINAL


def test_subject_token_for_sibling_only_is_not_advertised_not_unknown():
    """Presence via a sibling's subject token + watched subject absent →
    NOT_ADVERTISED (row c), even with no source-level token."""
    ev = _make(require_liveliness=True)
    ev.liveliness.add_subject("some_other_subject")
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_NOT_ADVERTISED


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


def test_evaluate_grouped_aggregates_worst_within_a_source():
    """Two subjects on the same source: source level is the worst of them."""
    good = _make(name="good")
    bad = _make(name="bad", inactive_after_s=1.0)
    for i in range(20):
        good.record(now=1000.0 + i * 0.1)
    bad.record(now=1000.0)
    overall, sources = evaluate_grouped(
        {("dev1", "good"): good, ("dev1", "bad"): bad}, now=1005.0
    )
    assert overall == HEALTH_INACTIVE
    assert len(sources) == 1
    src = sources[0]
    assert src.name == "dev1"
    assert src.level == HEALTH_INACTIVE
    assert {s.name for s in src.subjects} == {"good", "bad"}


def test_evaluate_grouped_one_subject_per_source():
    """Two sources, one subject each → two SourceStates, entity worst of both."""
    healthy = _make(inactive_after_s=5.0)
    inactive = _make(inactive_after_s=1.0)
    for i in range(20):
        healthy.record(now=1000.0 + i * 0.1)
    inactive.record(now=1000.0)
    overall, sources = evaluate_grouped(
        {("dev_a", "x"): healthy, ("dev_b", "x"): inactive}, now=1002.0
    )
    assert overall == HEALTH_INACTIVE
    by_name = {s.name: s for s in sources}
    assert set(by_name) == {"dev_a", "dev_b"}
    assert by_name["dev_a"].level == HEALTH_NOMINAL
    assert by_name["dev_b"].level == HEALTH_INACTIVE


def test_evaluate_grouped_empty_is_unknown():
    overall, sources = evaluate_grouped({}, now=100.0)
    assert overall == HEALTH_UNKNOWN
    assert sources == []


def test_source_state_defaults():
    s = SourceState(name="dev", level=HEALTH_NOMINAL)
    assert s.subjects == []


# --- Tiered band tests ----------------------------------------------------


def _band_eval(value, **rule_kwargs):
    rule = ContentRule(field="value", **rule_kwargs)
    return rule.evaluate(SimpleNamespace(value=value))[0]


_TIERED_BANDS = [
    Band(level=HEALTH_NOMINAL, min=12, max=14.5),
    Band(level=HEALTH_DEGRADED, min=11, max=15),
    Band(level=HEALTH_CRITICAL, min=10, max=16),
]


@pytest.mark.parametrize(
    "value,expected",
    [
        (13.0, HEALTH_NOMINAL),
        (11.5, HEALTH_DEGRADED),
        (15.5, HEALTH_CRITICAL),
    ],
)
def test_band_tiered_match(value, expected):
    assert _band_eval(value, bands=_TIERED_BANDS) == expected


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
    msg.fix_type = LocationFixQuality.GPS_DR  # int 5

    rule = ContentRule(
        field="fix_type",
        bands=[
            Band(level=HEALTH_NOMINAL, equals=["GPS_DR", "FIX_3D"]),
            Band(level=HEALTH_DEGRADED, equals=["FIX_2D"]),
        ],
        default_level=HEALTH_CRITICAL,
    )
    assert rule.evaluate(msg)[0] == HEALTH_NOMINAL

    msg.fix_type = LocationFixQuality.INVALID
    assert rule.evaluate(msg)[0] == HEALTH_CRITICAL


def test_protobuf_enum_still_matches_by_int():
    from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality

    msg = LocationFixQuality()
    msg.fix_type = LocationFixQuality.GPS_DR
    rule = ContentRule(
        field="fix_type",
        bands=[Band(level=HEALTH_NOMINAL, equals=[5])],
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


def test_protobuf_subject_health_has_checks_field():
    """SubjectHealth proto must expose a repeated CheckResult `checks` field."""
    from keelson.payloads.EntityHealth_pb2 import (
        CheckResult as ProtoCheckResult,
        SubjectHealth,
        HEALTH_NOMINAL as PROTO_NOMINAL,
    )

    sh = SubjectHealth()
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
    ev.liveliness.add_source_token("k/a")
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


def test_subject_state_has_empty_checks_by_default():
    """SubjectState should expose a `checks` list, defaulting to empty."""
    state = SubjectState(name="x", level=HEALTH_NOMINAL)
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


class TestTokenCoversSource:
    """A token vouches for its own source and everything sub-qualified beneath it.

    Regression: the liveliness subscriber used to be declared on the subject's
    DATA key, and a token's single `*` matches exactly one segment — so
    `pubsub/*/mavlink` intersects `pubsub/location_fix/mavlink` but never
    `pubsub/sensor_status/mavlink/gps`. Ten of the drone's eleven sources sat at
    level UNKNOWN while reporting a measured rate of 1.0 Hz.
    """

    def test_exact_source(self):
        from entity_health.evaluator import token_covers_source

        assert token_covers_source("mavlink", "mavlink")

    def test_sub_qualified_source(self):
        from entity_health.evaluator import token_covers_source

        assert token_covers_source("mavlink", "mavlink/gps")
        assert token_covers_source("mavlink", "mavlink/gps/raw")

    def test_prefix_must_be_on_a_segment_boundary(self):
        from entity_health.evaluator import token_covers_source

        assert not token_covers_source("mavlink", "mavlink2")

    def test_a_narrower_token_does_not_cover_its_parent(self):
        from entity_health.evaluator import token_covers_source

        assert not token_covers_source("mavlink/gps", "mavlink")

    def test_multi_segment_token_source(self):
        """Real source ids contain slashes: "srv-herakles/kystverket", "ins/3/sbg"."""
        from entity_health.evaluator import token_covers_source

        assert token_covers_source(
            "srv-herakles/kystverket", "srv-herakles/kystverket/ais"
        )
        assert not token_covers_source("srv-herakles/kystverket", "srv-herakles")

    def test_empty_inputs(self):
        from entity_health.evaluator import token_covers_source

        assert not token_covers_source("", "mavlink")
        assert not token_covers_source("mavlink", "")


# --- three-tier liveliness state machine (rows a-d) ----------------------
#
# Evaluator.evaluate(now), require_liveliness=True:
#   a. no source presence at all                              -> UNKNOWN
#   b. present, subject advertised                             -> full eval
#   c. present, subject not advertised, other subjects are      -> NOT_ADVERTISED
#   d. present, no subjects advertised at all (legacy source)   -> full eval
#      (transitional fallback, indistinguishable from (b) here)


def test_row_a_no_source_presence_is_unknown():
    """(a) source_tokens empty → UNKNOWN, no checks, regardless of
    advertised_subjects (which can't be populated without presence anyway)."""
    live = SourceLiveliness()
    ev = _make(require_liveliness=True)
    ev.liveliness = live
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_UNKNOWN
    assert state.checks == []


def test_row_b_present_and_advertised_is_full_eval():
    """(b) source present + this subject advertised → normal activity/rate
    evaluation, same as the pre-three-tier "alive" path."""
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/*/dev1")
    live.add_subject("x")  # exp.name == "x" (see _make's default)
    ev = _make(require_liveliness=True)
    ev.liveliness = live
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_NOMINAL
    assert [c.name for c in state.checks] == ["activity", "publication_rate"]


def test_row_b_present_and_advertised_but_silent_is_inactive():
    """(b) still runs the normal activity gate — advertised but silent is
    INACTIVE, not NOT_ADVERTISED. This is the distinction the whole
    three-tier design exists to make."""
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/*/dev1")
    live.add_subject("x")
    ev = _make(require_liveliness=True)
    ev.liveliness = live
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_INACTIVE


def test_row_c_present_but_other_subject_advertised_is_not_advertised():
    """(c) source is a three-tier adopter (advertises >=1 subject) but not
    this one → NOT_ADVERTISED with a single explanatory check."""
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/*/dev1")
    live.add_subject("other_subject")
    ev = _make(require_liveliness=True)  # name="x"
    ev.liveliness = live
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_NOT_ADVERTISED
    assert [c.name for c in state.checks] == ["advertised"]
    check = state.checks[0]
    assert check.level == HEALTH_NOT_ADVERTISED
    assert "1 subject" in check.detail
    assert "check subject name / source_id" in check.detail


def test_row_c_detail_counts_multiple_advertised_subjects():
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/*/dev1")
    live.add_subject("other_a")
    live.add_subject("other_b")
    live.add_subject("other_c")
    ev = _make(require_liveliness=True)  # name="x"
    ev.liveliness = live
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_NOT_ADVERTISED
    assert "3 subject" in state.checks[0].detail


def test_row_c_not_advertised_even_when_samples_were_previously_recorded():
    """NOT_ADVERTISED is a pure liveliness-set outcome — pre-existing sample
    history doesn't leak through and force a full eval."""
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/*/dev1")
    live.add_subject("other_subject")
    ev = _make(require_liveliness=True)
    ev.liveliness = live
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_NOT_ADVERTISED
    assert [c.name for c in state.checks] == ["advertised"]


def test_row_d_present_but_no_subjects_advertised_falls_back_to_full_eval():
    """(d) legacy source: only source-level presence, advertised_subjects
    entirely empty → can't distinguish "not configured" from "configured but
    silent", so fall back to activity-based evaluation like before."""
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/pubsub/*/dev1")  # legacy coarse token
    ev = _make(require_liveliness=True)
    ev.liveliness = live
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_NOMINAL
    assert [c.name for c in state.checks] == ["activity", "publication_rate"]


def test_row_d_present_no_subjects_advertised_no_samples_is_inactive():
    live = SourceLiveliness()
    live.add_source_token("keelson/@v0/e/pubsub/*/dev1")
    ev = _make(require_liveliness=True)
    ev.liveliness = live
    state = ev.evaluate(now=100.0)
    assert state.level == HEALTH_INACTIVE


def test_require_liveliness_false_bypasses_liveliness_entirely():
    """require_liveliness=False: liveliness is never consulted, even if the
    shared SourceLiveliness says the source is absent."""
    live = SourceLiveliness()  # empty: no presence, no advertised subjects
    ev = _make(require_liveliness=False)
    ev.liveliness = live
    for i in range(20):
        ev.record(now=1000.0 + i * 0.1)
    state = ev.evaluate(now=1000.0 + 2.0)
    assert state.level == HEALTH_NOMINAL


def test_source_liveliness_add_remove_subject_roundtrip():
    live = SourceLiveliness()
    assert live.advertised_subjects == set()
    live.add_subject("a")
    live.add_subject("b")
    assert live.advertised_subjects == {"a", "b"}
    live.remove_subject("a")
    assert live.advertised_subjects == {"b"}
    # Removing something never added is a no-op, not an error.
    live.remove_subject("does-not-exist")
    assert live.advertised_subjects == {"b"}


def test_source_liveliness_add_remove_source_token_roundtrip():
    live = SourceLiveliness()
    assert not live.is_present
    live.add_source_token("k1")
    assert live.is_present
    live.add_source_token("k2")
    live.remove_source_token("k1")
    assert live.is_present  # k2 still present
    live.remove_source_token("k2")
    assert not live.is_present


def test_multiple_evaluators_share_one_source_liveliness_instance():
    """The whole point of SourceLiveliness being shared: one source-level
    presence signal drives every subject Evaluator for that source, and
    per-subject advertisement is independent."""
    live = SourceLiveliness()
    live.add_source_token("k/a")
    live.add_subject("x")
    ev_x = Evaluator(Expectation(name="x", require_liveliness=True), liveliness=live)
    ev_y = Evaluator(Expectation(name="y", require_liveliness=True), liveliness=live)

    # x is advertised -> row (b); y is not, but x is -> row (c)
    state_x = ev_x.evaluate(now=100.0)
    state_y = ev_y.evaluate(now=100.0)
    assert state_x.level == HEALTH_INACTIVE  # advertised, present, no samples yet
    assert state_y.level == HEALTH_NOT_ADVERTISED

    # Source process dies -> its Zenoh session drops EVERY token it held
    # (source-level and subject-level alike) -> both go UNKNOWN. Note a
    # still-live subject token would count as presence on its own, since
    # tokens die with the session that declared them.
    live.remove_source_token("k/a")
    live.remove_subject("x")
    assert ev_x.evaluate(now=100.0).level == HEALTH_UNKNOWN
    assert ev_y.evaluate(now=100.0).level == HEALTH_UNKNOWN


# --- NOT_ADVERTISED rollup semantics / worst() regression ------------------


def test_not_advertised_is_a_diagnostic_not_a_fault_in_rollups():
    """NOT_ADVERTISED denotes a watch config error (typo / stale watch),
    not a fault of the monitored system — it must never mask a real fault
    level on a sibling subject in the aggregate."""
    assert worst(HEALTH_NOT_ADVERTISED, HEALTH_CRITICAL) == HEALTH_CRITICAL
    assert worst(HEALTH_NOT_ADVERTISED, HEALTH_INACTIVE) == HEALTH_INACTIVE
    assert worst(HEALTH_NOT_ADVERTISED, HEALTH_DEGRADED) == HEALTH_DEGRADED
    assert worst(HEALTH_NOT_ADVERTISED, HEALTH_NOMINAL) == HEALTH_NOMINAL


def test_not_advertised_surfaces_when_only_diagnostics_exist():
    """With no fault levels present, NOT_ADVERTISED wins over UNKNOWN —
    it's the more informative (resolved) of the two diagnostics."""
    assert worst(HEALTH_NOT_ADVERTISED) == HEALTH_NOT_ADVERTISED
    assert worst(HEALTH_UNKNOWN, HEALTH_NOT_ADVERTISED) == HEALTH_NOT_ADVERTISED
    assert worst(HEALTH_NOT_ADVERTISED, HEALTH_UNKNOWN) == HEALTH_NOT_ADVERTISED


def test_worst_still_ignores_unknown():
    assert worst(HEALTH_UNKNOWN, HEALTH_NOMINAL) == HEALTH_NOMINAL
    assert worst(HEALTH_UNKNOWN, HEALTH_UNKNOWN) == HEALTH_UNKNOWN
    assert worst(HEALTH_UNKNOWN) == HEALTH_UNKNOWN


def test_worst_regression_full_ordering():
    """Fault-level worst→best ordering is unchanged: INACTIVE, CRITICAL,
    DEGRADED, NOMINAL. Diagnostics (NOT_ADVERTISED, UNKNOWN) are excluded
    from the aggregate whenever any fault level exists."""
    levels = [
        HEALTH_NOMINAL,
        HEALTH_DEGRADED,
        HEALTH_CRITICAL,
        HEALTH_INACTIVE,
        HEALTH_NOT_ADVERTISED,
    ]
    assert worst(*levels) == HEALTH_INACTIVE
    assert worst(HEALTH_NOMINAL, HEALTH_DEGRADED) == HEALTH_DEGRADED
    assert worst(HEALTH_DEGRADED, HEALTH_CRITICAL) == HEALTH_CRITICAL
    assert worst(HEALTH_CRITICAL, HEALTH_INACTIVE) == HEALTH_INACTIVE
    assert worst(HEALTH_NOMINAL) == HEALTH_NOMINAL


def test_evaluate_grouped_keeps_not_advertised_per_subject_only():
    """The mistyped watch stays fully visible at subject level, but the
    source and entity aggregates reflect the real state of the correctly
    watched sibling (the alarm-feed signal keeps its dynamic range)."""
    live = SourceLiveliness()
    live.add_source_token("k/a")
    live.add_subject("y")  # only "y" is advertised

    ev_x = Evaluator(
        Expectation(name="x", inactive_after_s=1.0, require_liveliness=True),
        liveliness=live,
    )
    ev_y = Evaluator(
        Expectation(name="y", inactive_after_s=1.0, require_liveliness=True),
        liveliness=live,
    )
    ev_y.record(now=1000.0)  # y is advertised, alive, and active

    overall, sources = evaluate_grouped(
        {("dev1", "x"): ev_x, ("dev1", "y"): ev_y}, now=1000.2
    )
    assert overall == HEALTH_NOMINAL
    assert len(sources) == 1
    src = sources[0]
    assert src.level == HEALTH_NOMINAL
    by_name = {s.name: s for s in src.subjects}
    assert by_name["x"].level == HEALTH_NOT_ADVERTISED
    assert by_name["y"].level == HEALTH_NOMINAL


def test_evaluate_grouped_all_not_advertised_source_reports_it():
    """A source whose every watched subject is unadvertised aggregates to
    NOT_ADVERTISED (nothing real to report instead)."""
    live = SourceLiveliness()
    live.add_source_token("k/a")
    live.add_subject("something_else")

    ev_x = Evaluator(Expectation(name="x", require_liveliness=True), liveliness=live)
    overall, sources = evaluate_grouped({("dev1", "x"): ev_x}, now=100.0)
    assert overall == HEALTH_NOT_ADVERTISED
    assert sources[0].level == HEALTH_NOT_ADVERTISED


def test_parse_level_accepts_not_advertised():
    assert parse_level("NOT_ADVERTISED") == HEALTH_NOT_ADVERTISED
    assert parse_level("HEALTH_NOT_ADVERTISED") == HEALTH_NOT_ADVERTISED
    assert parse_level(HEALTH_NOT_ADVERTISED) == HEALTH_NOT_ADVERTISED
