"""Layer 2 aggregation: health levels -> composite score -> authority level."""

import pytest

from entity_health.authority import (
    AUTHORITY_ASSISTED_AUTONOMOUS,
    AUTHORITY_FULL_AUTONOMOUS,
    AUTHORITY_MINIMAL_SAFE_MODE,
    AUTHORITY_REMOTE_CONTROLLED,
    AUTHORITY_SUPERVISED_REMOTE,
    AUTHORITY_UNKNOWN,
    coverage_for,
    evaluate_authority,
    is_scored,
    level_for,
    score_for,
)
from entity_health.evaluator import (
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_INACTIVE,
    HEALTH_NOMINAL,
    HEALTH_NOT_ADVERTISED,
    HEALTH_UNKNOWN,
    worst,
)


class Subj:
    """Stand-in for evaluator.SubjectState — only name and level are read."""

    def __init__(self, name, level):
        self.name = name
        self.level = level


class Src:
    """Stand-in for evaluator.SourceState.

    `subjects` is optional: a source built without one is treated as fully
    covered, which is what the score-only tests below are about.
    """

    def __init__(self, name, level, subjects=None):
        self.name = name
        self.level = level
        self.subjects = subjects or []


def src_of(name, *subject_levels):
    """A source whose roll-up comes from the real `worst()`.

    Building the level rather than passing it keeps these tests honest about
    what `evaluate_grouped()` would actually hand `evaluate_authority()` — the
    whole coverage problem lives in what `worst()` does to a mixed subject set.
    """
    subjects = [Subj(f"{name}.{i}", lv) for i, lv in enumerate(subject_levels)]
    return Src(name, worst(*subject_levels), subjects)


class TestComponentScores:
    @pytest.mark.parametrize(
        "level,expected",
        [
            (HEALTH_NOMINAL, 1.0),
            (HEALTH_DEGRADED, 0.5),
            (HEALTH_CRITICAL, 0.0),
            (HEALTH_INACTIVE, 0.0),
            (HEALTH_UNKNOWN, 0.0),
        ],
    )
    def test_score_table(self, level, expected):
        assert score_for(level) == expected

    def test_unrecognised_level_scores_zero(self):
        """An unknown enum value is not evidence of health."""
        assert score_for(99) == 0.0

    @pytest.mark.parametrize(
        "level",
        [
            HEALTH_NOMINAL,
            HEALTH_DEGRADED,
            HEALTH_CRITICAL,
            HEALTH_INACTIVE,
            HEALTH_UNKNOWN,
            99,
        ],
    )
    def test_every_level_but_one_is_scored(self, level):
        assert is_scored(level)

    def test_not_advertised_is_excluded_rather_than_scored(self):
        """It is a fact about this monitor's config, not about the vessel."""
        assert not is_scored(HEALTH_NOT_ADVERTISED)


class TestLadder:
    @pytest.mark.parametrize(
        "score,level",
        [
            (1.0, AUTHORITY_FULL_AUTONOMOUS),
            (0.85, AUTHORITY_FULL_AUTONOMOUS),
            (0.8499, AUTHORITY_ASSISTED_AUTONOMOUS),
            (0.65, AUTHORITY_ASSISTED_AUTONOMOUS),
            (0.6499, AUTHORITY_REMOTE_CONTROLLED),
            (0.45, AUTHORITY_REMOTE_CONTROLLED),
            (0.4499, AUTHORITY_SUPERVISED_REMOTE),
            (0.25, AUTHORITY_SUPERVISED_REMOTE),
            (0.2499, AUTHORITY_MINIMAL_SAFE_MODE),
            (0.0, AUTHORITY_MINIMAL_SAFE_MODE),
        ],
    )
    def test_every_boundary(self, score, level):
        """Each threshold is inclusive at its lower bound."""
        assert level_for(score) == level


class TestUnknownCountsAsFailed:
    """The policy this module exists to encode.

    Excluding non-reporting components from the mean is the obvious-looking
    alternative and would let a nearly blind vessel declare full autonomy.
    """

    def test_a_mostly_silent_vessel_does_not_score_high(self):
        sources = [Src("gnss", HEALTH_NOMINAL)] + [
            Src(f"s{i}", HEALTH_UNKNOWN) for i in range(4)
        ]
        a = evaluate_authority(sources)
        assert a.composite_score == pytest.approx(0.2)
        assert a.level == AUTHORITY_MINIMAL_SAFE_MODE

    def test_one_silent_component_of_five_only_dents_it(self):
        sources = [Src(f"s{i}", HEALTH_NOMINAL) for i in range(4)] + [
            Src("quiet", HEALTH_UNKNOWN)
        ]
        a = evaluate_authority(sources)
        assert a.composite_score == pytest.approx(0.8)
        assert a.level == AUTHORITY_ASSISTED_AUTONOMOUS

    def test_losing_everything_is_all_stop(self):
        a = evaluate_authority([Src(f"s{i}", HEALTH_UNKNOWN) for i in range(3)])
        assert a.composite_score == 0.0
        assert a.level == AUTHORITY_MINIMAL_SAFE_MODE


class TestEvaluate:
    def test_all_nominal_is_full_autonomy(self):
        a = evaluate_authority([Src("a", HEALTH_NOMINAL), Src("b", HEALTH_NOMINAL)])
        assert a.composite_score == 1.0
        assert a.level == AUTHORITY_FULL_AUTONOMOUS
        assert a.reason == "all 2 components nominal"

    def test_component_scores_are_keyed_by_source_name(self):
        a = evaluate_authority(
            [Src("gnss_main", HEALTH_DEGRADED), Src("battery", HEALTH_NOMINAL)]
        )
        assert a.component_scores == {"gnss_main": 0.5, "battery": 1.0}
        assert a.composite_score == pytest.approx(0.75)

    def test_no_components_is_UNKNOWN_not_all_stop(self):
        """An empty config has no opinion; it must not assert all-stop."""
        a = evaluate_authority([])
        assert a.level == AUTHORITY_UNKNOWN
        assert a.reason == "no components configured"


class TestReason:
    def test_names_the_worst_first(self):
        a = evaluate_authority(
            [
                Src("battery", HEALTH_DEGRADED),
                Src("gnss", HEALTH_UNKNOWN),
                Src("imu", HEALTH_NOMINAL),
            ]
        )
        # gnss scores 0.0, battery 0.5 — the worse one leads.
        assert a.reason.startswith("gnss not reporting")
        assert "battery degraded" in a.reason
        assert "imu" not in a.reason

    def test_summarises_beyond_three(self):
        a = evaluate_authority([Src(f"s{i}", HEALTH_CRITICAL) for i in range(6)])
        assert "and 3 more" in a.reason

    def test_distinguishes_silent_from_failed(self):
        """ "not reporting" and "critical" send someone to different places."""
        silent = evaluate_authority([Src("x", HEALTH_UNKNOWN)]).reason
        failed = evaluate_authority([Src("x", HEALTH_CRITICAL)]).reason
        assert silent == "x not reporting"
        assert failed == "x critical"


class TestHysteresis:
    """The level is sticky, and the stickiness is asymmetric.

    The FULL_AUTONOMOUS threshold is 0.85 and the margin 0.05, so:

        below 0.80   fall out of FULL
        0.80 - 0.85  hold FULL if already there
        0.85 - 0.90  hold ASSISTED if already there
        0.90 and up  climb into FULL
    """

    def test_cold_start_matches_the_bare_ladder(self):
        """No previous level -> unchanged behaviour, so a restart is not a step change."""
        for score in (0.0, 0.2499, 0.25, 0.6499, 0.85, 1.0):
            assert level_for(score, None) == level_for(score)

    def test_unknown_previous_is_treated_as_no_previous(self):
        assert level_for(0.86, AUTHORITY_UNKNOWN) == AUTHORITY_FULL_AUTONOMOUS

    @pytest.mark.parametrize("score", [0.80, 0.83, 0.8499])
    def test_holds_the_higher_level_inside_the_band(self, score):
        """Crossing 0.85 downward is not enough to give up FULL_AUTONOMOUS."""
        assert level_for(score, AUTHORITY_FULL_AUTONOMOUS) == AUTHORITY_FULL_AUTONOMOUS

    @pytest.mark.parametrize("score", [0.85, 0.88, 0.8999])
    def test_holds_the_lower_level_inside_the_band(self, score):
        """Reaching 0.85 from below is not enough to claim FULL_AUTONOMOUS."""
        assert (
            level_for(score, AUTHORITY_ASSISTED_AUTONOMOUS)
            == AUTHORITY_ASSISTED_AUTONOMOUS
        )

    def test_a_real_drop_still_drops(self):
        assert (
            level_for(0.7999, AUTHORITY_FULL_AUTONOMOUS)
            == AUTHORITY_ASSISTED_AUTONOMOUS
        )

    def test_a_real_climb_still_climbs(self):
        assert (
            level_for(0.90, AUTHORITY_ASSISTED_AUTONOMOUS) == AUTHORITY_FULL_AUTONOMOUS
        )

    def test_a_collapse_falls_all_the_way(self):
        """Hysteresis must not act as a ratchet on the way down."""
        assert level_for(0.0, AUTHORITY_FULL_AUTONOMOUS) == AUTHORITY_MINIMAL_SAFE_MODE

    def test_a_recovery_climbs_all_the_way(self):
        assert level_for(1.0, AUTHORITY_MINIMAL_SAFE_MODE) == AUTHORITY_FULL_AUTONOMOUS

    def test_flapping_sensor_does_not_flap_the_level(self):
        """The case this exists for, built from real sources rather than raw scores.

        Eight sources: five nominal, two known-degraded, and one oscillating
        NOMINAL <-> DEGRADED. That puts the composite at 0.875 and 0.8125
        alternately — astride the 0.85 FULL_AUTONOMOUS threshold and inside its
        0.80-0.90 band, which is the situation a vessel carrying a couple of
        degraded sensors is actually in.
        """
        steady = [Src(f"s{i}", HEALTH_NOMINAL) for i in range(5)]
        steady += [
            Src("known_bad_1", HEALTH_DEGRADED),
            Src("known_bad_2", HEALTH_DEGRADED),
        ]

        level = None
        seen = []
        for i in range(10):
            flapping = Src("flapper", HEALTH_NOMINAL if i % 2 else HEALTH_DEGRADED)
            level = evaluate_authority([*steady, flapping], level).level
            seen.append(level)

        assert set(seen) == {AUTHORITY_ASSISTED_AUTONOMOUS}

    def test_without_hysteresis_the_same_sequence_would_flap(self):
        """Guards the premise: without the previous level it really does chatter."""
        steady = [Src(f"s{i}", HEALTH_NOMINAL) for i in range(5)]
        steady += [
            Src("known_bad_1", HEALTH_DEGRADED),
            Src("known_bad_2", HEALTH_DEGRADED),
        ]

        seen = []
        for i in range(10):
            flapping = Src("flapper", HEALTH_NOMINAL if i % 2 else HEALTH_DEGRADED)
            seen.append(evaluate_authority([*steady, flapping]).level)

        # Ten ticks, two different declared autonomy levels, nothing actually
        # changed about the vessel except one sensor blinking.
        assert set(seen) == {AUTHORITY_FULL_AUTONOMOUS, AUTHORITY_ASSISTED_AUTONOMOUS}

    def test_sequence_settles_after_a_genuine_climb(self):
        level = None
        for score in (0.10, 0.30, 0.55, 0.72, 0.91, 0.86, 0.83, 0.81):
            level = level_for(score, level)
        # Climbed past 0.90 into FULL, then held it through the 0.80-0.85 band.
        assert level == AUTHORITY_FULL_AUTONOMOUS

    def test_evaluate_authority_threads_the_previous_level(self):
        """The public entry point, not just the ladder helper."""
        sources = [Src("a", HEALTH_NOMINAL), Src("b", HEALTH_DEGRADED)]
        # composite 0.75 -> bare ASSISTED; coming down from FULL it is held,
        # because 0.75 is not below 0.85 - 0.05.
        assert evaluate_authority(sources).level == AUTHORITY_ASSISTED_AUTONOMOUS
        held = evaluate_authority(sources, AUTHORITY_FULL_AUTONOMOUS)
        assert held.level == AUTHORITY_ASSISTED_AUTONOMOUS
        # ...and the score it reports is untouched by the hysteresis.
        assert held.composite_score == pytest.approx(0.75)


class TestNotAdvertisedIsExcluded:
    """A watch-config typo must not lower what the vessel claims it can do.

    `worst()` already drops NOT_ADVERTISED from health rollups — "a stale watch
    or typo must not pin the source/entity aggregate" — but it still *returns*
    the level when only diagnostics exist. Scoring that at the 0.0 default
    silently re-included, one level down, exactly what evaluator.py excluded.
    """

    def test_a_typod_source_does_not_dent_a_healthy_vessel(self):
        """The reviewer's scenario: six sources, five nominal, one typo'd."""
        sources = [Src(f"s{i}", HEALTH_NOMINAL) for i in range(5)]
        sources.append(Src("typo", HEALTH_NOT_ADVERTISED))

        authority = evaluate_authority(sources)

        assert authority.composite_score == pytest.approx(1.0)
        assert authority.level == AUTHORITY_FULL_AUTONOMOUS

    def test_scoring_it_zero_would_have_cost_a_level(self):
        """Guards the premise, so the fix cannot rot into a no-op."""
        five_nominal_one_zero = 5 / 6
        assert level_for(five_nominal_one_zero) == AUTHORITY_ASSISTED_AUTONOMOUS

    def test_excluded_sources_are_absent_from_component_scores(self):
        """Excluded means excluded — not present with a made-up score."""
        authority = evaluate_authority(
            [Src("good", HEALTH_NOMINAL), Src("typo", HEALTH_NOT_ADVERTISED)]
        )

        assert set(authority.component_scores) == {"good"}

    def test_but_the_reason_still_names_them(self):
        """The prose is the only place the operator can see the typo at all."""
        authority = evaluate_authority(
            [Src("good", HEALTH_NOMINAL), Src("typo", HEALTH_NOT_ADVERTISED)]
        )

        assert (
            authority.reason
            == "all 1 components nominal; typo not advertised (excluded)"
        )

    def test_it_is_not_called_unhealthy(self):
        """It has its own phrase now; the fallback wording was wrong twice over."""
        reason = evaluate_authority([Src("typo", HEALTH_NOT_ADVERTISED)]).reason

        assert "unhealthy" not in reason
        assert "not advertised" in reason

    def test_a_wholly_unassessed_config_is_UNKNOWN_not_all_stop(self):
        """Same argument as the empty-config case: nothing was assessed."""
        authority = evaluate_authority(
            [Src("a", HEALTH_NOT_ADVERTISED), Src("b", HEALTH_NOT_ADVERTISED)]
        )

        assert authority.level == AUTHORITY_UNKNOWN
        assert authority.composite_score == 0.0
        assert authority.component_scores == {}
        assert "not advertised" in authority.reason

    def test_exclusion_does_not_mask_a_genuine_fault(self):
        """It leaves the mean; it does not sweeten it."""
        authority = evaluate_authority(
            [
                Src("good", HEALTH_NOMINAL),
                Src("dead", HEALTH_CRITICAL),
                Src("typo", HEALTH_NOT_ADVERTISED),
            ]
        )

        # Mean over the two assessed sources, not three.
        assert authority.composite_score == pytest.approx(0.5)
        assert "dead critical" in authority.reason


class TestClimbHasNoDeadZone:
    """A vessel that recovers must actually be allowed to say so.

    Gating the climb on the *bare* target level's threshold alone leaves a band
    the ladder can never leave: a steady composite of 0.87 has bare
    FULL_AUTONOMOUS, misses 0.85 + 0.05, and holds MINIMAL_SAFE_MODE forever —
    never climbing even to ASSISTED_AUTONOMOUS, which 0.87 clears by a wide
    margin. Silent under-claiming is the same operator-distrust failure the
    margin exists to prevent, and 0.875 (eight sources, two degraded) is an
    entirely ordinary state to be in.
    """

    def test_a_recovery_into_the_top_band_settles_one_rung_down(self):
        assert (
            level_for(0.87, AUTHORITY_MINIMAL_SAFE_MODE)
            == AUTHORITY_ASSISTED_AUTONOMOUS
        )

    def test_and_it_converges_rather_than_oscillating(self):
        """One call is not enough: the bug was that it never resolved."""
        level = AUTHORITY_MINIMAL_SAFE_MODE
        seen = []
        for _ in range(10):
            level = level_for(0.87, level)
            seen.append(level)

        assert set(seen) == {AUTHORITY_ASSISTED_AUTONOMOUS}

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.30, AUTHORITY_SUPERVISED_REMOTE),
            (0.50, AUTHORITY_REMOTE_CONTROLLED),
            (0.70, AUTHORITY_ASSISTED_AUTONOMOUS),
            (0.90, AUTHORITY_FULL_AUTONOMOUS),
        ],
    )
    def test_climbs_to_the_highest_level_the_score_earns(self, score, expected):
        assert level_for(score, AUTHORITY_MINIMAL_SAFE_MODE) == expected

    def test_the_climb_still_stops_short_of_an_unearned_level(self):
        """Fixing the dead zone must not weaken the burden of proof."""
        assert level_for(0.87, AUTHORITY_MINIMAL_SAFE_MODE) != AUTHORITY_FULL_AUTONOMOUS

    def test_it_never_climbs_below_where_it_started(self):
        assert level_for(0.86, AUTHORITY_ASSISTED_AUTONOMOUS) == (
            AUTHORITY_ASSISTED_AUTONOMOUS
        )

    @pytest.mark.parametrize(
        "previous,score,expected",
        [
            # 0.45 + 0.05 == 0.5 exactly; 0.65 + 0.05 == 0.7000000000000001.
            # Both boundaries must behave the same way.
            (AUTHORITY_SUPERVISED_REMOTE, 0.50, AUTHORITY_REMOTE_CONTROLLED),
            (AUTHORITY_REMOTE_CONTROLLED, 0.70, AUTHORITY_ASSISTED_AUTONOMOUS),
            (AUTHORITY_ASSISTED_AUTONOMOUS, 0.90, AUTHORITY_FULL_AUTONOMOUS),
            (AUTHORITY_MINIMAL_SAFE_MODE, 0.30, AUTHORITY_SUPERVISED_REMOTE),
        ],
    )
    def test_a_threshold_landed_on_exactly_still_climbs(
        self, previous, score, expected
    ):
        """Decimal thresholds plus a decimal margin do not sum to a decimal."""
        assert level_for(score, previous) == expected

    def test_a_hair_below_the_boundary_still_does_not(self):
        """The epsilon absorbs float error, not a real shortfall."""
        assert level_for(0.699, AUTHORITY_REMOTE_CONTROLLED) == (
            AUTHORITY_REMOTE_CONTROLLED
        )


class TestCoverage:
    """A level says how bad it was; it cannot also say how much was looked at.

    `worst()` correctly refuses to let UNKNOWN mask a CRITICAL sibling, but the
    consequence is that a source reporting one NOMINAL subject and four silent
    ones rolls up to NOMINAL. Scoring that 1.0 defeats this module's headline
    policy one level down: the vessel that lost four of five sensors declares
    full autonomy after all, from inside a source rather than across them.
    """

    def test_the_premise_worst_rolls_a_mostly_dark_source_up_to_nominal(self):
        """Guard the premise, so the fix cannot quietly become a no-op."""
        levels = [HEALTH_NOMINAL] + [HEALTH_UNKNOWN] * 4
        assert worst(*levels) == HEALTH_NOMINAL
        assert score_for(worst(*levels)) == 1.0

    def test_a_mostly_dark_fleet_no_longer_declares_full_autonomy(self):
        """The PR's own justifying scenario, relocated one level down."""
        sources = [
            src_of(f"s{i}", HEALTH_NOMINAL, *[HEALTH_UNKNOWN] * 4) for i in range(5)
        ]

        a = evaluate_authority(sources)

        assert a.composite_score == pytest.approx(0.2)
        assert a.level == AUTHORITY_MINIMAL_SAFE_MODE

    def test_full_coverage_is_unchanged(self):
        """Nothing moves for a source that answered everything it was asked."""
        sources = [
            src_of("a", HEALTH_NOMINAL, HEALTH_NOMINAL),
            src_of("b", HEALTH_NOMINAL),
        ]

        a = evaluate_authority(sources)

        assert a.composite_score == 1.0
        assert a.level == AUTHORITY_FULL_AUTONOMOUS

    @pytest.mark.parametrize("known_bad", [HEALTH_CRITICAL, HEALTH_INACTIVE])
    def test_a_known_failure_is_evidence_not_missing_evidence(self, known_bad):
        """CRITICAL and INACTIVE already score zero; they must not also
        reduce coverage, or a confirmed dead sensor is punished twice while a
        merely silent one is punished once."""
        assert coverage_for([Subj("a", HEALTH_NOMINAL), Subj("b", known_bad)]) == 1.0

    def test_only_unknown_reduces_coverage(self):
        assert coverage_for(
            [Subj("a", HEALTH_NOMINAL), Subj("b", HEALTH_UNKNOWN)]
        ) == pytest.approx(0.5)

    def test_degraded_counts_as_assessed(self):
        assert coverage_for([Subj("a", HEALTH_DEGRADED)]) == 1.0

    def test_not_advertised_subjects_leave_the_denominator(self):
        """Same policy as at source level: a config error counts neither way."""
        assert (
            coverage_for([Subj("a", HEALTH_NOMINAL), Subj("b", HEALTH_NOT_ADVERTISED)])
            == 1.0
        )

    def test_a_typod_subject_does_not_dent_an_otherwise_covered_source(self):
        source = src_of("gnss", HEALTH_NOMINAL, HEALTH_NOT_ADVERTISED)

        a = evaluate_authority([source])

        assert a.composite_score == 1.0
        assert a.level == AUTHORITY_FULL_AUTONOMOUS

    def test_an_all_config_error_source_is_not_full_coverage(self):
        """The dangerous default: nothing measurable must not read as perfect."""
        assert coverage_for([Subj("a", HEALTH_NOT_ADVERTISED)]) is None

    def test_and_such_a_source_does_not_participate(self):
        a = evaluate_authority(
            [
                src_of("good", HEALTH_NOMINAL),
                src_of("typo", HEALTH_NOT_ADVERTISED, HEALTH_NOT_ADVERTISED),
            ]
        )

        assert set(a.component_scores) == {"good"}
        assert a.composite_score == 1.0
        assert "not advertised (excluded)" in a.reason

    def test_a_source_with_no_subjects_is_treated_as_fully_covered(self):
        """Not reachable from evaluate_grouped(), but callers may pass a bare
        level and must not be silently penalised for it."""
        a = evaluate_authority([Src("bare", HEALTH_NOMINAL)])

        assert a.composite_score == 1.0

    def test_coverage_and_severity_compose(self):
        """Half-degraded and half-covered is a quarter, not a half."""
        a = evaluate_authority([src_of("s", HEALTH_DEGRADED, HEALTH_UNKNOWN)])

        assert a.composite_score == pytest.approx(0.25)


class TestEqualSourceWeighting:
    """Averaging over subjects instead of sources would make the number of
    diagnostics a source happens to expose into a safety weight.

    This is the A1-vs-A2 choice, and it is the reason the composite is a mean
    over sources rather than over subjects.
    """

    def test_a_chatty_healthy_source_cannot_outvote_a_failed_one(self):
        chatty = src_of("chatty", *[HEALTH_NOMINAL] * 10)
        failed = src_of("failed", HEALTH_CRITICAL)

        a = evaluate_authority([chatty, failed])

        # One vote each: 0.5, not 10/11 = 0.909.
        assert a.composite_score == pytest.approx(0.5)
        assert a.composite_score != pytest.approx(10 / 11)

    def test_subject_count_does_not_change_a_healthy_source_s_weight(self):
        one = evaluate_authority(
            [src_of("a", HEALTH_NOMINAL), src_of("b", HEALTH_DEGRADED)]
        )
        many = evaluate_authority(
            [src_of("a", *[HEALTH_NOMINAL] * 8), src_of("b", HEALTH_DEGRADED)]
        )

        assert one.composite_score == many.composite_score == pytest.approx(0.75)


class TestCoverageInTheReason:
    """The prose has to agree with the score about *why* it dropped.

    A partially-covered source is reported NOMINAL by `worst()`. Saying only
    "all components nominal" while the score sits at 0.2 is the disagreement
    that makes an operator stop trusting the message.
    """

    def test_a_partially_assessed_source_is_named(self):
        a = evaluate_authority(
            [src_of("gnss", HEALTH_NOMINAL, HEALTH_UNKNOWN, HEALTH_UNKNOWN)]
        )

        assert "partially assessed" in a.reason
        assert "gnss 33%" in a.reason

    def test_it_does_not_claim_everything_is_nominal(self):
        a = evaluate_authority([src_of("gnss", HEALTH_NOMINAL, HEALTH_UNKNOWN)])

        assert a.composite_score == pytest.approx(0.5)
        assert a.reason != "all 1 components nominal"

    def test_a_fully_covered_nominal_fleet_says_so_plainly(self):
        a = evaluate_authority(
            [src_of("a", HEALTH_NOMINAL), src_of("b", HEALTH_NOMINAL)]
        )

        assert a.reason == "all 2 components nominal"

    def test_failures_still_lead_and_coverage_follows(self):
        a = evaluate_authority(
            [
                src_of("battery", HEALTH_CRITICAL),
                src_of("gnss", HEALTH_NOMINAL, HEALTH_UNKNOWN),
            ]
        )

        assert a.reason.startswith("battery critical")
        assert "partially assessed (gnss 50%)" in a.reason

    def test_beyond_three_partial_sources_are_summarised(self):
        sources = [src_of(f"s{i}", HEALTH_NOMINAL, HEALTH_UNKNOWN) for i in range(5)]

        a = evaluate_authority(sources)

        assert "and 2 more" in a.reason
