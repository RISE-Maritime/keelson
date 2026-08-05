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
