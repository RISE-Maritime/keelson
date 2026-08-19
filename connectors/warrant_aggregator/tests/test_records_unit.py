"""Reconstruction from the record stream alone."""

from pathlib import Path

import pytest

from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import ClaimGraph
from warrant_aggregator.records import format_record, reconstruct
from warrant_aggregator.wire import event_from_warrant_record, warrant_record_from_event
from test_engine_unit import GNSS_DARK, make_eh, steady

pytestmark = pytest.mark.unit

EXAMPLE_GRAPH = Path(__file__).resolve().parents[1] / "example-graph.yaml"
S = int(1e9)


def test_reconstruction_from_wire_round_trip():
    """Events -> WarrantRecord messages -> events -> state at T, with the
    justification text carried by snapshots, no config file needed."""
    events = []
    graph = ClaimGraph.load(EXAMPLE_GRAPH)
    engine = WarrantEngine(graph, events.append)
    t = steady(engine)
    engine.feed(t * S, make_eh(GNSS_DARK))
    engine.feed((t + 1) * S, make_eh(GNSS_DARK))

    wire = [
        event_from_warrant_record(record)
        for record in (warrant_record_from_event(e) for e in events)
        if record is not None
    ]

    before = reconstruct(wire, (t - 1) * S)
    assert before["claims"]["navigation"]["standing"] == "LICENSED"

    after = reconstruct(wire, (t + 2) * S)
    assert after["claims"]["navigation"]["standing"] == "WITHDRAWN"
    assert after["claims"]["gnss_fix"]["standing"] == "WITHDRAWN"
    fired = after["claims"]["gnss_fix"]["rebuttals_fired"]
    assert fired[0]["id"] == "fix_stream_not_nominal"

    text = format_record(after, ["navigation", "gnss_fix"])
    assert "[WITHDRAWN] navigation: the vessel can navigate" in text
    assert "warrant:" in text
    assert "fix_stream_not_nominal" in text
    assert "INACTIVE" in text
