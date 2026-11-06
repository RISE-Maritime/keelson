"""control_gate/v1 GateState.last_fence_cause: why the gate last moved its fence alone, so a manager that was
down tells a direct Abort automation from a CONN lapse and learns who asked (crowsnest-dev#290)."""

import pytest


@pytest.mark.unit
def test_the_gate_state_names_the_cause_of_its_last_fence():
    from keelson.interfaces.ControlGate_pb2 import FenceCause, GateState

    st = GateState()
    assert not st.HasField("last_fence_cause")  # optional: presence says whether there is one
    st.last_fence_cause.kind = FenceCause.KIND_ABORT_AUTOMATION
    st.last_fence_cause.fence.term, st.last_fence_cause.fence.generation = "a" * 32, 7
    st.last_fence_cause.requested_by = "ted@ROC-1"
    st.last_fence_cause.assignment_revision = 4
    back = GateState()
    back.ParseFromString(st.SerializeToString())
    c = back.last_fence_cause
    assert back.HasField("last_fence_cause")
    assert (c.kind, c.fence.generation, c.requested_by, c.assignment_revision) == (FenceCause.KIND_ABORT_AUTOMATION, 7, "ted@ROC-1", 4)


@pytest.mark.unit
def test_every_kind_the_protocol_names_exists():
    from keelson.interfaces.ControlGate_pb2 import FenceCause

    assert {k for k in FenceCause.Kind.keys()} == {
        "KIND_UNSPECIFIED", "KIND_ABORT_AUTOMATION", "KIND_CONN_LAPSE", "KIND_INVALID_SOURCE", "KIND_LOCAL_FALLBACK"}
