"""Round-trip and contract tests for the wire layer."""

from pathlib import Path

import pytest

from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.payloads.WarrantRecord_pb2 import WarrantRecord

from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import ClaimGraph
from warrant_aggregator.wire import (
    event_from_warrant_record,
    operational_authority_from_state,
    policy_config_digest,
    validate_ladder_names,
    warrant_record_from_event,
)
from test_engine_unit import GNSS_DARK, S, make_eh, steady

pytestmark = pytest.mark.unit

EXAMPLE_GRAPH = Path(__file__).resolve().parents[1] / "example-graph.yaml"


def run_to_withdrawal():
    events = []
    graph = ClaimGraph.load(EXAMPLE_GRAPH)
    engine = WarrantEngine(graph, events.append)
    t = steady(engine)
    engine.feed(t * S, make_eh(GNSS_DARK))
    return engine, events, graph


def test_standing_transition_round_trips():
    _engine, events, _graph = run_to_withdrawal()
    event = [e for e in events if e["kind"] == "standing" and e["claim"] == "gnss_fix"][
        -1
    ]
    record = warrant_record_from_event(event)
    assert record.WhichOneof("event") == "standing_transition"
    back = event_from_warrant_record(record)
    assert back["claim"] == event["claim"]
    assert back["from"] == event["from"]
    assert back["to"] == event["to"]
    assert back["rebuttals_fired"] == event["rebuttals_fired"]
    assert back["grounds"] == event["grounds"]


def test_reduction_transition_round_trips():
    _engine, events, _graph = run_to_withdrawal()
    event = [e for e in events if e["kind"] == "standing" and e["claim"] == "position"][
        -1
    ]
    assert event["to"] == "REDUCED"
    back = event_from_warrant_record(warrant_record_from_event(event))
    assert back["to"] == "REDUCED"
    assert back["grounds"] == {"gnss_fix": "WITHDRAWN", "gnss_aux_fix": "LICENSED"}


def test_snapshot_round_trips_with_justification_and_policy():
    _engine, events, _graph = run_to_withdrawal()
    event = [e for e in events if e["kind"] == "snapshot"][-1]
    event = {
        **event,
        "policy_config_digest": b"\x01\x02".hex(),
        "policy_id": "warrant_graph/v1",
    }
    record = warrant_record_from_event(event)
    assert record.WhichOneof("event") == "snapshot"
    assert record.snapshot.policy_config_digest == b"\x01\x02"
    assert record.snapshot.policy_id == "warrant_graph/v1"
    back = event_from_warrant_record(record)
    for name, claim in event["claims"].items():
        assert back["claims"][name]["standing"] == claim["standing"]
        assert back["claims"][name]["statement"] == claim["statement"]
        assert back["claims"][name]["warrant"] == claim["warrant"]
        assert back["claims"][name]["backing"] == claim["backing"]


def test_level_events_do_not_ride_the_record_stream():
    _engine, events, _graph = run_to_withdrawal()
    level_event = [e for e in events if e["kind"] == "level"][-1]
    assert warrant_record_from_event(level_event) is None


def test_operational_authority_publishes_no_scores():
    engine, _events, _graph = run_to_withdrawal()
    msg = operational_authority_from_state(engine, 123 * S, "warrant_graph/v1", b"d")
    assert msg.level == OperationalAuthority.AuthorityLevel.Value(
        "AUTHORITY_LEVEL_SUPERVISED_REMOTE"
    )
    assert not msg.HasField("composite_score")
    assert not msg.HasField("authority_score")
    assert msg.policy_id == "warrant_graph/v1"
    constraints = {c.component_id: c for c in msg.active_constraints}
    # Withdrawn claims only: the reduced position is not a constraint.
    assert set(constraints) == {"gnss_fix", "navigation"}
    assert (
        constraints["gnss_fix"].cause
        == OperationalAuthority.AuthorityConstraint.Cause.CAUSE_INACTIVE
    )
    assert (
        constraints["navigation"].cause
        == OperationalAuthority.AuthorityConstraint.Cause.CAUSE_UNSPECIFIED
    )
    assert "withdrawn" in msg.reason


def test_ladder_names_must_be_authority_levels(tmp_path):
    graph_text = (EXAMPLE_GRAPH.read_text()).replace(
        "name: FULL_AUTONOMOUS", "name: TOTALLY_FINE"
    )
    path = tmp_path / "bad.yaml"
    path.write_text(graph_text)
    graph = ClaimGraph.load(path)
    with pytest.raises(ValueError, match="TOTALLY_FINE"):
        validate_ladder_names(graph)


def test_policy_config_digest_is_stable(tmp_path):
    assert policy_config_digest(EXAMPLE_GRAPH) == policy_config_digest(EXAMPLE_GRAPH)


def test_target_standing_survives_the_round_trip():
    event = {
        "kind": "snapshot",
        "t_ns": 5 * 10**9,
        "level": "SUPERVISED_REMOTE",
        "claims": {
            "gnss_fix": {
                "standing": "WITHDRAWN",
                "target": "LICENSED",
                "since_ns": 4 * 10**9,
                "rebuttals_fired": [],
                "grounds": {},
                "statement": "s",
                "warrant": "w",
                "backing": "b",
            }
        },
    }
    record = warrant_record_from_event(event)
    assert record.snapshot.claims[0].target_standing == (
        WarrantRecord.Standing.STANDING_LICENSED
    )
    back = event_from_warrant_record(record)
    assert back["claims"]["gnss_fix"]["target"] == "LICENSED"
    assert back["claims"]["gnss_fix"]["standing"] == "WITHDRAWN"


def test_record_without_target_standing_reads_as_not_recorded():
    """Records written before the field existed must not read as equality."""
    record = WarrantRecord()
    record.timestamp.FromNanoseconds(0)
    state = record.snapshot.claims.add()
    state.claim_id = "gnss_fix"
    state.standing = WarrantRecord.Standing.STANDING_WITHDRAWN
    assert event_from_warrant_record(record)["claims"]["gnss_fix"]["target"] is None
