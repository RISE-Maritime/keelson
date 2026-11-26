"""Evidence with numbers (keelson #237): a cause that needs a number printed
next to it is recorded on warrant_record, with the number and the limit.

The chain is entity_health's real configuration and evaluator grading a
measured value into bands, then this connector's engine and wire layer
recording the claim it withdraws. Nothing here is a new kind of rebuttal:
the example's two causes, #211's relief reserve and #210's C2-link floor,
both reach the record through an ordinary content rule.
"""

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
import yaml
from jsonschema import validate
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.payloads.WarrantRecord_pb2 import WarrantRecord
from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import ClaimGraph
from warrant_aggregator.wire import (
    operational_authority_from_state,
    warrant_record_from_event,
)

from entity_health.evaluator import Evaluator, evaluate_grouped

pytestmark = pytest.mark.unit

S = int(1e9)
CONNECTORS = Path(__file__).resolve().parents[2]
EXAMPLE = CONNECTORS / "warrant_aggregator" / "examples" / "evidence-with-numbers"

_loader = SourceFileLoader(
    "entity_health2keelson",
    str(CONNECTORS / "entity_health" / "bin" / "entity_health2keelson.py"),
)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
entity_health_bin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entity_health_bin)


def _health_config():
    config = json.loads((EXAMPLE / "entity-health.json").read_text())
    validate(config, entity_health_bin.JSON_SCHEMA)
    return config


def _graph():
    return ClaimGraph.from_spec(
        yaml.safe_load((EXAMPLE / "claim-graph.yaml").read_text())
    )


class _Vessel:
    """entity_health's evaluators for the example, fed readings by second."""

    def __init__(self):
        config = _health_config()
        entity_health_bin.CONFIG = config
        self.evaluators = {
            key: Evaluator(exp)
            for key, exp in entity_health_bin._flatten_expectations(config).items()
        }

    def read(self, t: int, charge_pct: float, sinr_db: float):
        for key, value in (
            (("bms", "battery_state_of_charge_pct"), charge_pct),
            (("satcom", "radio_sinr_db"), sinr_db),
        ):
            sample = TimestampedFloat()
            sample.value = value
            self.evaluators[key].record(float(t), sample)
        overall, sources = evaluate_grouped(self.evaluators, float(t))
        return entity_health_bin._build_entity_health(overall, sources, t * S)


def _run(readings):
    """Feed (charge, sinr) one per second after a nominal warm-up long enough
    to license every claim; return the engine and its events."""
    events = []
    graph = _graph()
    engine = WarrantEngine(graph, events.append)
    vessel = _Vessel()
    t = 0
    for _ in range(2 * int(graph.requalification_hold_s) + 3):
        engine.feed(t * S, vessel.read(t, 60.0, 9.0))
        t += 1
    assert engine.states["endurance_reserve"].standing == 2  # LICENSED
    for charge, sinr in readings:
        engine.feed(t * S, vessel.read(t, charge, sinr))
        t += 1
    return engine, events


def _withdrawal(events, claim):
    [event] = [
        e
        for e in events
        if e["kind"] == "standing" and e["claim"] == claim and e["to"] == "WITHDRAWN"
    ]
    return warrant_record_from_event(event).standing_transition


def test_the_example_configs_are_valid():
    _health_config()
    graph = _graph()
    assert {"endurance_reserve", "satcom_link"} <= set(graph.claims)


def test_a_battery_below_the_relief_floor_is_recorded_with_both_numbers():
    """#211: B refuses the handoff because its charge is below the reserve."""
    engine, events = _run([(18.4, 9.0)])
    transition = _withdrawal(events, "endurance_reserve")
    assert WarrantRecord.Standing.Name(transition.from_standing) == "STANDING_LICENSED"
    [fired] = transition.rebuttals_fired
    assert fired.id == "below_relief_reserve"
    assert fired.evidence_level == "DEGRADED"
    # The measured value and the limit it broke, side by side.
    assert "value=18.4" in fired.evidence
    assert "NOMINAL is ≥ 25.0" in fired.evidence


def test_an_sinr_below_the_c2_floor_is_recorded_with_both_numbers():
    """#210: the C2 link fails over because satcom's SINR fell below the floor."""
    engine, events = _run([(60.0, 1.8)])
    [fired] = _withdrawal(events, "satcom_link").rebuttals_fired
    assert fired.id == "sinr_below_c2_floor"
    assert "value=1.8" in fired.evidence
    assert "NOMINAL is ≥ 4.0" in fired.evidence


def test_the_determination_points_at_the_record():
    """The determination stays small and typed; the why is one hop away."""
    engine, _events = _run([(18.4, 9.0)])
    msg = operational_authority_from_state(
        engine, 0, "warrant_graph/v1", b"d", warrant_source_id="warrant"
    )
    assert msg.level == OperationalAuthority.AuthorityLevel.Value(
        "AUTHORITY_LEVEL_SUPERVISED_REMOTE"
    )
    [constraint] = msg.active_constraints
    assert (constraint.warrant.source_id, constraint.warrant.claim_id) == (
        "warrant",
        "endurance_reserve",
    )
    # No number on the determination: it lives in the record.
    assert "18.4" not in msg.reason
