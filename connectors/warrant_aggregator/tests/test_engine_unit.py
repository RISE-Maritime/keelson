"""Unit tests for the warrant engine: propagation, weakening through
redundancy, non-compensatory aggregation, the burden-of-proof asymmetry,
staleness, and sink-failure rollback."""

import pathlib
import tempfile

import pytest
import yaml

from keelson.payloads.EntityHealth_pb2 import EntityHealth, HealthLevel

from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import STANDING_NAMES, ClaimGraph

pytestmark = pytest.mark.unit

EXAMPLE_GRAPH = pathlib.Path(__file__).resolve().parents[1] / "example-graph.yaml"
S = int(1e9)

ALL_NOMINAL = {
    ("gnss", "location_fix"): "NOMINAL",
    ("gnss_aux", "location_fix"): "NOMINAL",
    ("compass", "heading_true_north_deg"): "NOMINAL",
}
GNSS_DARK = dict(ALL_NOMINAL)
GNSS_DARK[("gnss", "location_fix")] = "INACTIVE"
BOTH_GNSS_DARK = dict(GNSS_DARK)
BOTH_GNSS_DARK[("gnss_aux", "location_fix")] = "INACTIVE"


def make_eh(levels):
    msg = EntityHealth()
    sources = {}
    for (source, subject), level in levels.items():
        sources.setdefault(source, []).append((subject, level))
    for source, subjects in sources.items():
        s = msg.sources.add()
        s.name = source
        for subject, level in subjects:
            sub = s.subjects.add()
            sub.name = subject
            sub.level = HealthLevel.Value(f"HEALTH_{level}")
            sub.measured_publication_rate_hz = 1.0
    return msg


def make_engine():
    events = []
    graph = ClaimGraph.load(EXAMPLE_GRAPH)
    return WarrantEngine(graph, events.append), events, graph


def standings(engine):
    return {n: STANDING_NAMES[s.standing] for n, s in engine.states.items()}


def steady(engine, t0=0):
    hold = engine.graph.requalification_hold_s
    t = t0
    # Source claims license after one hold, the level after a second.
    for _ in range(2 * int(hold) + 3):
        engine.feed(t * S, make_eh(ALL_NOMINAL))
        t += 1
    return t


def test_burden_of_proof_at_startup():
    engine, _events, _graph = make_engine()
    engine.feed(0, make_eh(ALL_NOMINAL))
    assert set(standings(engine).values()) == {"WITHDRAWN"}
    assert engine.level == "MINIMAL_SAFE_MODE"


def test_steady_state_licenses_everything():
    engine, _events, _graph = make_engine()
    steady(engine)
    assert set(standings(engine).values()) == {"LICENSED"}
    assert engine.level == "FULL_AUTONOMOUS"


def test_withdrawal_weakens_redundant_ground_and_drops_level():
    engine, events, _graph = make_engine()
    t = steady(engine)
    engine.feed(t * S, make_eh(GNSS_DARK))
    got = standings(engine)
    assert got["gnss_fix"] == "WITHDRAWN"
    assert got["gnss_aux_fix"] == "LICENSED"
    assert got["compass_heading"] == "LICENSED"
    # One of two redundant members licensed: weakened, not withdrawn.
    assert got["position"] == "WEAKENED"
    # Non-compensatory: the licensed compass cannot offset position's
    # standing falling below navigation's LICENSED requirement.
    assert got["navigation"] == "WITHDRAWN"
    # The level drop is immediate: no hysteresis downward.
    assert engine.level == "SUPERVISED_REMOTE"
    withdrawal = [
        e
        for e in events
        if e["kind"] == "standing"
        and e["claim"] == "gnss_fix"
        and e["to"] == "WITHDRAWN"
    ][-1]
    fired = withdrawal["rebuttals_fired"]
    assert fired[0]["id"] == "fix_stream_not_nominal"
    assert fired[0]["evidence_level"] == "INACTIVE"
    weakening = [
        e for e in events if e["kind"] == "standing" and e["claim"] == "position"
    ][-1]
    assert weakening["to"] == "WEAKENED"
    assert weakening["grounds"] == {
        "gnss_fix": "WITHDRAWN",
        "gnss_aux_fix": "LICENSED",
    }


def test_redundancy_exhausted_withdraws():
    engine, _events, _graph = make_engine()
    t = steady(engine)
    engine.feed(t * S, make_eh(BOTH_GNSS_DARK))
    got = standings(engine)
    assert got["position"] == "WITHDRAWN"
    assert got["navigation"] == "WITHDRAWN"
    assert engine.level == "MINIMAL_SAFE_MODE"


def test_requalification_is_guarded():
    engine, _events, _graph = make_engine()
    t = steady(engine)
    engine.feed(t * S, make_eh(GNSS_DARK))
    assert engine.level == "SUPERVISED_REMOTE"
    hold = engine.graph.requalification_hold_s
    engine.feed((t + 1) * S, make_eh(ALL_NOMINAL))
    assert standings(engine)["gnss_fix"] == "WITHDRAWN"
    engine.feed((t + 2 + hold) * S, make_eh(ALL_NOMINAL))
    assert standings(engine)["gnss_fix"] == "LICENSED"
    assert standings(engine)["navigation"] == "LICENSED"
    # The level waits its own hold.
    assert engine.level == "SUPERVISED_REMOTE"
    engine.feed((t + 3 + 2 * hold) * S, make_eh(ALL_NOMINAL))
    assert engine.level == "FULL_AUTONOMOUS"


def test_stale_evidence_withdraws():
    engine, events, _graph = make_engine()
    t = steady(engine)
    engine.tick(int((t + engine.graph.evidence_max_age_s + 1) * S))
    assert set(standings(engine).values()) == {"WITHDRAWN"}
    assert engine.level == "MINIMAL_SAFE_MODE"
    withdrawal = [
        e for e in events if e["kind"] == "standing" and e["claim"] == "gnss_fix"
    ][-1]
    assert "absence of evidence" in withdrawal["rebuttals_fired"][0]["evidence"]


def test_snapshot_carries_justification_text():
    engine, events, _graph = make_engine()
    steady(engine)
    snapshot = [e for e in events if e["kind"] == "snapshot"][-1]
    claim = snapshot["claims"]["navigation"]
    assert claim["statement"] == "the vessel can navigate"
    assert claim["warrant"]
    assert claim["backing"]


def test_sink_failure_rolls_the_transition_back():
    fail = {"on": False}
    events = []

    def sink(event):
        if fail["on"]:
            raise RuntimeError("publish failed")
        events.append(event)

    graph = ClaimGraph.load(EXAMPLE_GRAPH)
    engine = WarrantEngine(graph, sink)
    hold = graph.requalification_hold_s
    t = 0
    for _ in range(2 * int(hold) + 3):
        engine.feed(t * S, make_eh(ALL_NOMINAL))
        t += 1
    fail["on"] = True
    with pytest.raises(RuntimeError):
        engine.feed(t * S, make_eh(GNSS_DARK))
    # The failed transition did not commit: state never runs ahead of the
    # record stream.
    assert standings(engine)["gnss_fix"] == "LICENSED"
    fail["on"] = False
    engine.feed((t + 1) * S, make_eh(GNSS_DARK))
    assert standings(engine)["gnss_fix"] == "WITHDRAWN"
    emitted = [
        e for e in events if e["kind"] == "standing" and e["claim"] == "gnss_fix"
    ][-1]
    assert emitted["to"] == "WITHDRAWN"


def test_zero_hold_upgrades_on_the_second_evaluation():
    # Pinned semantics: the candidate is recorded on one evaluation and
    # applied on a later one, so even with hold 0 an upgrade is never
    # applied at the instant its evidence first appears.
    spec = yaml.safe_load(EXAMPLE_GRAPH.read_text())
    spec["requalification_hold_s"] = 0.0
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "graph.yaml"
        path.write_text(yaml.safe_dump(spec))
        graph = ClaimGraph.load(path)
    engine = WarrantEngine(graph, lambda e: None)
    engine.feed(0, make_eh(ALL_NOMINAL))
    assert standings(engine)["gnss_fix"] == "WITHDRAWN"
    engine.feed(1, make_eh(ALL_NOMINAL))
    assert standings(engine)["gnss_fix"] == "LICENSED"
