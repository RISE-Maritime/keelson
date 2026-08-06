"""Layer 2 aggregation: health levels -> composite score -> authority level."""

import pytest

from entity_health.authority import (
    AUTHORITY_ASSISTED_AUTONOMOUS,
    AUTHORITY_FULL_AUTONOMOUS,
    AUTHORITY_MINIMAL_SAFE_MODE,
    AUTHORITY_REMOTE_CONTROLLED,
    AUTHORITY_SUPERVISED_REMOTE,
    AUTHORITY_UNKNOWN,
    evaluate_authority,
    level_for,
    score_for,
)
from entity_health.evaluator import (
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_INACTIVE,
    HEALTH_NOMINAL,
    HEALTH_UNKNOWN,
)


class Src:
    """Stand-in for evaluator.SourceState — only name and level are read."""

    def __init__(self, name, level):
        self.name = name
        self.level = level


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
        a = evaluate_authority([Src("gnss_main", HEALTH_DEGRADED), Src("battery", HEALTH_NOMINAL)])
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
        """"not reporting" and "critical" send someone to different places."""
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
        assert level_for(score, AUTHORITY_ASSISTED_AUTONOMOUS) == AUTHORITY_ASSISTED_AUTONOMOUS

    def test_a_real_drop_still_drops(self):
        assert level_for(0.7999, AUTHORITY_FULL_AUTONOMOUS) == AUTHORITY_ASSISTED_AUTONOMOUS

    def test_a_real_climb_still_climbs(self):
        assert level_for(0.90, AUTHORITY_ASSISTED_AUTONOMOUS) == AUTHORITY_FULL_AUTONOMOUS

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
        steady += [Src("known_bad_1", HEALTH_DEGRADED), Src("known_bad_2", HEALTH_DEGRADED)]

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
        steady += [Src("known_bad_1", HEALTH_DEGRADED), Src("known_bad_2", HEALTH_DEGRADED)]

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
