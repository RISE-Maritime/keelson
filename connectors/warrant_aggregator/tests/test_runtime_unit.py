"""Runtime reconfiguration without Zenoh: get_config answers with the loaded
document, a rejected document changes nothing, an accepted one restarts every
claim WITHDRAWN under the new digest, and the level falls to the floor."""

import pathlib

import pytest
import yaml
from test_engine_unit import ALL_NOMINAL, S, make_eh
from warrant_aggregator.model import ClaimGraph
from warrant_aggregator.runtime import Runtime
from warrant_aggregator.wire import policy_config_digest_of_spec

pytestmark = pytest.mark.unit

EXAMPLE_GRAPH = pathlib.Path(__file__).resolve().parents[1] / "example-graph.yaml"


def load_spec():
    return yaml.safe_load(EXAMPLE_GRAPH.read_text())


def make_runtime(clock="data"):
    records, authorities = [], []
    graph = ClaimGraph.load(EXAMPLE_GRAPH)
    rt = Runtime(
        graph,
        records.append,
        authorities.append,
        policy_id="warrant_graph/v1",
        clock=clock,
    )
    return rt, records, authorities


def run_to_steady(rt):
    hold = rt.graph.requalification_hold_s
    t = 0
    for _ in range(2 * int(hold) + 3):
        rt.feed(t * S, make_eh(ALL_NOMINAL))
        t += 1
    return t


def test_get_config_answers_with_the_loaded_document():
    rt, _r, _a = make_runtime()
    assert rt.get_config() == load_spec()
    # A copy: the caller cannot reach into the running graph.
    rt.get_config()["claims"].clear()
    assert rt.get_config() == load_spec()


def test_rejected_document_changes_nothing():
    rt, records, _a = make_runtime()
    run_to_steady(rt)
    before = (rt.graph, rt.engine, rt.digest, len(records))
    bad = load_spec()
    bad["claims"]["navigation"]["grounds"]["edges"][0]["claim"] = "nowhere"
    with pytest.raises(ValueError, match="unknown ground nowhere"):
        rt.set_config(bad)
    assert (rt.graph, rt.engine, rt.digest, len(records)) == before
    assert rt.engine.level == "FULL_AUTONOMOUS"


def test_structural_failures_are_value_errors_too():
    rt, _r, _a = make_runtime()
    with pytest.raises(ValueError):
        rt.set_config({"claims": {"a": {"tier": "component"}}, "autonomy_ladder": []})
    with pytest.raises(ValueError, match="mapping"):
        rt.set_config(["not", "a", "mapping"])


def test_accepted_document_restarts_every_claim_under_the_new_digest():
    rt, records, authorities = make_runtime()
    t = run_to_steady(rt)
    assert rt.engine.level == "FULL_AUTONOMOUS"

    new_spec = load_spec()
    new_spec["claims"]["navigation"]["grounds"]["edges"][0]["requires"] = "REDUCED"
    del records[:]
    del authorities[:]
    rt.set_config(new_spec)

    # Conclusions do not carry over.
    standings = {n: s.standing for n, s in rt.engine.states.items()}
    assert set(standings.values()) == {0}
    assert rt.engine.level == "MINIMAL_SAFE_MODE"
    # Facts do: the evidence seen before the swap is still there.
    assert rt.engine.evidence.keys() == {key for key in ALL_NOMINAL}

    # The first record under the new graph is a snapshot with the new digest,
    # and the determination that follows is the floor.
    snapshot = next(e for e in records if e["kind"] == "snapshot")
    assert (
        snapshot["policy_config_digest"] == policy_config_digest_of_spec(new_spec).hex()
    )
    assert rt.digest == policy_config_digest_of_spec(new_spec)
    assert authorities[-1].level == authorities[-1].AuthorityLevel.Value(
        "AUTHORITY_LEVEL_MINIMAL_SAFE_MODE"
    )
    assert rt.get_config() == new_spec

    # And the graph re-licenses under the new rules: the edited edge accepts REDUCED.
    for _ in range(2 * int(rt.graph.requalification_hold_s) + 3):
        rt.feed(t * S, make_eh(ALL_NOMINAL))
        t += 1
    assert rt.engine.level == "FULL_AUTONOMOUS"


def test_set_config_before_any_evidence_only_swaps():
    rt, records, authorities = make_runtime()
    rt.set_config(load_spec())
    # Nothing to evaluate and no clock axis yet: no record, no determination.
    assert records == []
    assert authorities == []
    rt.feed(0, make_eh(ALL_NOMINAL))
    assert any(e["kind"] == "snapshot" for e in records)
