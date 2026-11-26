"""The SARUMS control-method ceiling on the wire (keelson#471): published on
operational_authority when the graph has a control_method_ladder, absent when it
has none, and never on the warrant record."""

import pytest
from test_engine_unit import GNSS_DARK, S, make_eh, method_engine, steady
from test_wire_unit import run_to_withdrawal
from warrant_aggregator.wire import (
    operational_authority_from_state,
    warrant_record_from_event,
)

pytestmark = pytest.mark.unit


def test_no_method_ladder_publishes_no_ceiling():
    engine, _events, _graph = run_to_withdrawal()
    msg = operational_authority_from_state(engine, 123 * S, "warrant_graph/v1", b"d")
    assert not msg.HasField(
        "control_method_ceiling"
    ), "absent, never 0, when the policy does not speak SARUMS"


def test_the_ceiling_rides_operational_authority_and_not_the_record():

    engine, events = method_engine()
    t = steady(engine)
    assert (
        operational_authority_from_state(
            engine, t * S, "p", b"d"
        ).control_method_ceiling
        == 5
    )
    engine.feed(t * S, make_eh(GNSS_DARK))
    msg = operational_authority_from_state(engine, t * S, "p", b"d")
    assert msg.HasField("control_method_ceiling")
    assert msg.control_method_ceiling == 3
    method_event = [e for e in events if e["kind"] == "control_method"][-1]
    assert warrant_record_from_event(method_event) is None
