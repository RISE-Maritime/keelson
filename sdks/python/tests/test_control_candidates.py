"""The motion gate's data plane (protocols/control_gate.yaml): candidates in, ResolvedControl out."""

import pytest

from keelson import get_subject_schema


@pytest.mark.unit
def test_the_three_subjects_resolve():
    assert get_subject_schema("control_candidate") == "keelson.ControlCandidate"
    assert get_subject_schema("guidance_candidate") == "keelson.GuidanceCandidate"
    assert get_subject_schema("resolved_control") == "keelson.ResolvedControl"


@pytest.mark.unit
def test_both_candidates_carry_the_one_stream_header():
    from keelson.payloads.ControlCandidate_pb2 import ControlCandidate
    from keelson.payloads.GuidanceCandidate_pb2 import GuidanceCandidate

    for cls in (ControlCandidate, GuidanceCandidate):
        stream = cls.DESCRIPTOR.fields_by_name["stream"].message_type
        assert stream.full_name == "keelson.CandidateStream", cls
        assert {"stream_id", "sequence"} <= set(stream.fields_by_name)


@pytest.mark.unit
def test_a_helm_candidate_has_no_decision_basis_and_an_optional_fence():
    from keelson.payloads.ControlCandidate_pb2 import ControlCandidate

    fields = set(ControlCandidate.DESCRIPTOR.fields_by_name)
    assert {"source", "dof", "value_pct", "stream", "fence"} <= fields
    assert "nav_function" not in fields and "human_order" not in fields
    c = ControlCandidate(source="ROC-1/helm-1/rudder", dof="rudder", value_pct=0.0)
    assert not ControlCandidate.FromString(c.SerializeToString()).HasField("fence")


@pytest.mark.unit
def test_emitting_nothing_is_not_emitting_zero():
    """Absence is absence: an absent accepted_pct is the gate emitting nothing (a stop, the lockout)."""
    from keelson.payloads.ResolvedControl_pb2 import ResolvedControl, ResolvedDof

    nothing = ResolvedDof(dof="throttle")
    zero = ResolvedDof(dof="throttle", accepted_pct=0.0)
    rc = ResolvedControl(vessel_id="v", dofs=[nothing, zero], gate_id="control-gate")
    back = ResolvedControl.FromString(rc.SerializeToString())
    assert not back.dofs[0].HasField("accepted_pct")
    assert back.dofs[1].HasField("accepted_pct") and back.dofs[1].accepted_pct == 0.0
    assert not back.dofs[0].HasField("actual_pct")


@pytest.mark.unit
def test_the_states_and_feedback_qualities():
    from keelson.payloads.ResolvedControl_pb2 import ControlState, FeedbackQuality

    assert {
        "CONTROL_STATE_ACTIVE",
        "CONTROL_STATE_STALE",
        "CONTROL_STATE_TRANSITION",
        "CONTROL_STATE_INHIBITED",
        "CONTROL_STATE_UNMAPPED",
    } <= set(ControlState.keys())
    assert FeedbackQuality.Value("FEEDBACK_QUALITY_NONE") == 4
    assert ControlState.Value("CONTROL_STATE_UNSPECIFIED") == 0
