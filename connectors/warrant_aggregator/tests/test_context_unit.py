"""Context as evidence and coupled conditions (keelson #414)."""

from pathlib import Path

import pytest
import yaml
from keelson.payloads.Route_pb2 import Route
from keelson.payloads.RouteExecution_pb2 import RouteExecution
from test_engine_unit import S, make_eh
from warrant_aggregator.context import ContextResolver, leg_context
from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import (
    AllOf,
    AnyOf,
    ClaimGraph,
    ContextCondition,
    HealthCondition,
)

pytestmark = pytest.mark.unit

EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "context-envelope"
    / "claim-graph.yaml"
)
VIS = ("weather", "visibility_range_m")
GUST = ("weather", "true_wind_speed_max_mps")
NOMINAL = {VIS: "NOMINAL", GUST: "NOMINAL"}
OPEN_SEA = {
    "operational_context": "OPEN_SEA",
    "traffic_intensity": "LIGHT",
    "colreg_situation": None,
}


def _spec():
    return yaml.safe_load(EXAMPLE.read_text())


# --- parsing --------------------------------------------------------------


def test_the_example_parses_into_a_condition_tree():
    graph = ClaimGraph.from_spec(_spec())
    first, second = graph.claims["remote_navigation"].rebuttals
    assert first.when == AllOf(
        (
            ContextCondition("traffic_intensity", frozenset({"HEAVY"})),
            HealthCondition("weather", "visibility_range_m", "NOMINAL"),
        )
    )
    assert isinstance(second.when, AllOf)
    assert isinstance(second.when.parts[0], AnyOf)


@pytest.mark.parametrize(
    "when, message",
    [
        ({"all_of": []}, "non-empty list"),
        (
            {
                "any_of": [{"context": "traffic_intensity", "in": ["HEAVY"]}],
                "context": "x",
            },
            "exactly one",
        ),
        ({"context": "weather_mood", "in": ["GRUMPY"]}, "unknown context fact"),
        ({"context": "traffic_intensity", "in": ["GRIDLOCK"]}, "has no value"),
        ({"context": "traffic_intensity", "in": []}, "needs `in:`"),
        ({"context": "traffic_intensity", "in": ["HEAVY"], "near": 1}, "unknown key"),
        ({"source": "weather", "subject": "visibility_range_m"}, "needs level_below"),
        ({}, "exactly one"),
    ],
)
def test_a_malformed_condition_is_refused(when, message):
    spec = _spec()
    spec["claims"]["remote_navigation"]["rebuttals"][0]["when"] = when
    with pytest.raises(ValueError, match=message):
        ClaimGraph.from_spec(spec)


def test_a_plain_health_condition_still_parses():
    spec = _spec()
    spec["claims"]["remote_navigation"]["rebuttals"][0]["when"] = {
        "source": "weather",
        "subject": "visibility_range_m",
        "level_below": "NOMINAL",
    }
    rebuttal = ClaimGraph.from_spec(spec).claims["remote_navigation"].rebuttals[0]
    assert rebuttal.when == HealthCondition("weather", "visibility_range_m", "NOMINAL")


# --- evaluation -------------------------------------------------------------


def _licensed_engine(context=OPEN_SEA):
    """Run the example to steady state on nominal weather in `context`."""
    events = []
    graph = ClaimGraph.from_spec(_spec())
    engine = WarrantEngine(graph, events.append)
    t = 0
    for _ in range(2 * int(graph.requalification_hold_s) + 3):
        engine.feed_context(t * S, context, "test leg")
        engine.feed(t * S, make_eh(NOMINAL))
        t += 1
    assert engine.states["remote_navigation"].standing == 2, "never licensed"
    return engine, events, t


def _step(engine, t, levels, context):
    engine.feed_context(t * S, context, "test leg")
    engine.feed(t * S, make_eh(levels))
    return engine.states["remote_navigation"]


def test_poor_visibility_alone_in_open_water_withdraws_nothing():
    engine, _e, t = _licensed_engine()
    state = _step(engine, t, {**NOMINAL, VIS: "DEGRADED"}, OPEN_SEA)
    assert state.standing == 2


def test_heavy_traffic_alone_withdraws_nothing():
    engine, _e, t = _licensed_engine()
    state = _step(engine, t, NOMINAL, {**OPEN_SEA, "traffic_intensity": "HEAVY"})
    assert state.standing == 2


def test_the_coupled_limit_withdraws_and_records_both_halves():
    engine, events, t = _licensed_engine()
    state = _step(
        engine,
        t,
        {**NOMINAL, VIS: "DEGRADED"},
        {**OPEN_SEA, "traffic_intensity": "HEAVY"},
    )
    assert state.standing == 0
    [fired] = state.fired
    assert fired["id"] == "heavy_traffic_in_poor_visibility"
    assert "context traffic_intensity=HEAVY (test leg)" in fired["evidence"]
    assert " AND " in fired["evidence"]
    assert "weather/visibility_range_m DEGRADED" in fired["evidence"]
    assert fired["evidence_level"] == "DEGRADED"
    assert engine.level == "MINIMAL_SAFE_MODE"


@pytest.mark.parametrize(
    "context",
    [
        {**OPEN_SEA, "operational_context": "PORT_APPROACH"},
        {**OPEN_SEA, "traffic_intensity": "MODERATE"},
    ],
)
def test_any_of_either_context_couples_with_gusts(context):
    engine, _e, t = _licensed_engine()
    state = _step(engine, t, {**NOMINAL, GUST: "DEGRADED"}, context)
    assert [f["id"] for f in state.fired] == ["gusts_in_confined_or_busy_water"]


def test_an_unknown_context_counts_against_the_claim():
    """No voyage: the context is unknown, so poor visibility alone withdraws."""
    engine, _e, t = _licensed_engine()
    unknown = dict.fromkeys(OPEN_SEA)
    state = _step(engine, t, {**NOMINAL, VIS: "DEGRADED"}, unknown)
    assert state.standing == 0
    assert "context traffic_intensity: unknown" in state.fired[0]["evidence"]


def test_a_stale_context_counts_against_the_claim():
    engine, _e, t = _licensed_engine()
    # Health keeps arriving; the context stops. Past evidence_max_age_s it is unknown.
    for k in range(20):
        engine.feed((t + k) * S, make_eh({**NOMINAL, VIS: "DEGRADED"}))
    assert engine.states["remote_navigation"].standing == 0


def test_context_survives_a_reconfiguration_like_health_does():
    engine, _e, _t = _licensed_engine({**OPEN_SEA, "traffic_intensity": "HEAVY"})
    fresh = WarrantEngine(engine.graph, lambda e: None)
    fresh.evidence = dict(engine.evidence)
    assert ("@context", "traffic_intensity") in fresh.evidence


# --- resolving the leg ------------------------------------------------------


def _route():
    route = Route(route_id="r1", route_edition_number=3)
    a = route.waypoints.waypoint.add(id="wp-a", operational_context=4)  # PORT_APPROACH
    a.leg.colreg_traffic_intensity = 4  # HEAVY
    a.leg.colreg_situation = 2  # CROSSING_STBD
    route.waypoints.waypoint.add(id="wp-b")  # unlabelled, no leg
    return route


def test_leg_context_reads_the_departure_waypoint_and_its_leg():
    assert leg_context(_route(), "wp-a") == {
        "operational_context": "PORT_APPROACH",
        "traffic_intensity": "HEAVY",
        "colreg_situation": "CROSSING_STBD",
    }


def test_an_unlabelled_or_missing_leg_is_unknown():
    unknown = dict.fromkeys(
        ("operational_context", "traffic_intensity", "colreg_situation")
    )
    assert leg_context(_route(), "wp-b") == unknown
    assert leg_context(_route(), "wp-zz") == unknown


def _execution(route_id="r1", edition=3, leg="wp-a"):
    msg = RouteExecution(current_leg_from_waypoint_id=leg)
    msg.route_ref.route_id = route_id
    msg.route_ref.route_edition_number = edition
    return msg


def test_the_resolver_reads_the_named_edition_once():
    calls = []

    def fetch(route_id, edition):
        calls.append((route_id, edition))
        return _route() if (route_id, edition) == ("r1", 3) else None

    resolver = ContextResolver(fetch)
    facts, detail = resolver.resolve(_execution())
    assert facts["traffic_intensity"] == "HEAVY"
    assert detail == "route r1 edition 3, leg from wp-a"
    resolver.resolve(_execution())
    assert calls == [("r1", 3)]  # editions are immutable: cached

    # A different edition is fetched, never assumed equal; a miss is not cached.
    facts, detail = resolver.resolve(_execution(edition=4))
    assert set(facts.values()) == {None}
    assert "could not be resolved" in detail
    resolver.resolve(_execution(edition=4))
    assert calls == [("r1", 3), ("r1", 4), ("r1", 4)]


def test_no_route_being_sailed_is_unknown():
    facts, detail = ContextResolver(lambda *a: None).resolve(RouteExecution())
    assert set(facts.values()) == {None}
    assert detail == "no route or leg being sailed"
