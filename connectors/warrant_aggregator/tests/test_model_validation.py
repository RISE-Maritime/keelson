"""Config validation: cycles, the ladder floor, requires_any semantics,
and name typos raising ValueError rather than KeyError."""

import pathlib

import pytest
import yaml

from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import ClaimGraph
from test_engine_unit import ALL_NOMINAL, BOTH_GNSS_DARK, make_eh

pytestmark = pytest.mark.unit

EXAMPLE_GRAPH = pathlib.Path(__file__).resolve().parents[1] / "example-graph.yaml"
S = int(1e9)


def load_spec():
    return yaml.safe_load(EXAMPLE_GRAPH.read_text())


def write(tmp_path, spec):
    path = tmp_path / "graph.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def test_cycle_detection(tmp_path):
    spec = load_spec()
    spec["claims"]["a"] = {
        "tier": "intermediate",
        "statement": "a",
        "warrant": "w",
        "backing": "b",
        "grounds": {"edges": [{"claim": "b", "requires": "LICENSED"}]},
    }
    spec["claims"]["b"] = {
        "tier": "intermediate",
        "statement": "b",
        "warrant": "w",
        "backing": "b",
        "grounds": {"edges": [{"claim": "a", "requires": "LICENSED"}]},
    }
    with pytest.raises(ValueError, match="cycle"):
        ClaimGraph.load(write(tmp_path, spec))


def test_floor_must_be_unconditional(tmp_path):
    spec = load_spec()
    spec["autonomy_ladder"][-1]["requires"] = {"navigation": "WEAKENED"}
    with pytest.raises(ValueError, match="floor"):
        ClaimGraph.load(write(tmp_path, spec))


def test_requires_any_alternatives(tmp_path):
    spec = load_spec()
    # SUPERVISED_REMOTE achievable through either alternative.
    spec["autonomy_ladder"][1] = {
        "name": "SUPERVISED_REMOTE",
        "requires_any": [
            {"position": "WEAKENED"},
            {"compass_heading": "LICENSED"},
        ],
    }
    graph = ClaimGraph.load(write(tmp_path, spec))
    engine = WarrantEngine(graph, lambda e: None)
    t = 0
    for _ in range(2 * int(graph.requalification_hold_s) + 3):
        engine.feed(t * S, make_eh(ALL_NOMINAL))
        t += 1
    engine.feed(t * S, make_eh(BOTH_GNSS_DARK))
    # position is WITHDRAWN, but the compass alternative still holds.
    assert engine.level == "SUPERVISED_REMOTE"


def test_requires_any_empty_is_unsatisfiable(tmp_path):
    spec = load_spec()
    spec["autonomy_ladder"][1] = {
        "name": "SUPERVISED_REMOTE",
        "requires_any": [],
    }
    graph = ClaimGraph.load(write(tmp_path, spec))
    engine = WarrantEngine(graph, lambda e: None)
    t = 0
    for _ in range(2 * int(graph.requalification_hold_s) + 3):
        engine.feed(t * S, make_eh(ALL_NOMINAL))
        t += 1
    engine.feed(t * S, make_eh(BOTH_GNSS_DARK))
    # An explicitly empty requires_any is unsatisfiable, never
    # unconstrained: the rung is skipped and the level falls to the floor.
    assert engine.level == "MINIMAL_SAFE_MODE"


def test_unknown_standing_name_raises_value_error(tmp_path):
    spec = load_spec()
    spec["claims"]["navigation"]["grounds"]["edges"][0]["requires"] = "SOLID"
    with pytest.raises(ValueError, match="unknown standing 'SOLID'"):
        ClaimGraph.load(write(tmp_path, spec))


def test_unknown_health_level_raises_value_error(tmp_path):
    spec = load_spec()
    spec["claims"]["gnss_fix"]["rebuttals"][0]["when"]["level_below"] = "FINE"
    with pytest.raises(ValueError, match="unknown health level 'FINE'"):
        ClaimGraph.load(write(tmp_path, spec))
