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


@pytest.mark.unit
def test_a_helm_candidate_carries_its_calibration_and_its_own_health():
    from keelson.payloads.ControlCandidate_pb2 import ControlCandidate
    from keelson.payloads.EntityHealth_pb2 import HealthLevel

    fields = ControlCandidate.DESCRIPTOR.fields_by_name
    assert (fields["profile_version"].number, fields["device_health"].number) == (7, 8)
    assert fields["device_health"].enum_type.full_name == "keelson.HealthLevel"
    c = ControlCandidate(
        source="ROC-1/helm-1/rudder",
        dof="rudder",
        value_pct=-12.5,
        profile_version="ssrov/3",
        device_health=HealthLevel.HEALTH_DEGRADED,
    )
    back = ControlCandidate.FromString(c.SerializeToString())
    assert (back.profile_version, back.device_health) == (
        "ssrov/3",
        HealthLevel.HEALTH_DEGRADED,
    )
    # Never sent as UNKNOWN: a reader seeing 0 knows the publisher predates the field.
    assert ControlCandidate().device_health == HealthLevel.HEALTH_UNKNOWN


@pytest.mark.unit
def test_a_resolved_dof_names_the_sample_it_accepted_and_when():
    """Provenance for latency (crowsnest-dev#98): t1 sample, t2 publish, the source's clock quality,
    t3 the gate's receipt and t3→t4 its hold on its own clock. Absent when nothing is accepted.
    """
    from keelson.payloads.GuidanceCandidate_pb2 import ClockQuality
    from keelson.payloads.ResolvedControl_pb2 import ResolvedDof

    f = ResolvedDof.DESCRIPTOR.fields_by_name
    want = {
        "source_stream_id": (13, f["source_stream_id"].TYPE_STRING),
        "source_sequence": (14, f["source_sequence"].TYPE_UINT64),
        "source_sample_time": (15, f["source_sample_time"].TYPE_MESSAGE),
        "source_published_at": (16, f["source_published_at"].TYPE_MESSAGE),
        "source_clock_quality": (17, f["source_clock_quality"].TYPE_ENUM),
        "gate_received_at": (18, f["gate_received_at"].TYPE_MESSAGE),
        "gate_hold_ms": (19, f["gate_hold_ms"].TYPE_UINT32),
    }
    assert {k: (f[k].number, f[k].type) for k in want} == want
    assert f["source_clock_quality"].enum_type.full_name == "keelson.ClockQuality"
    assert {
        f[k].message_type.full_name
        for k in ("source_sample_time", "source_published_at", "gate_received_at")
    } == {"google.protobuf.Timestamp"}
    assert set(f) == {
        "dof",
        "requested_pct",
        "accepted_pct",
        "actual_pct",
        "control_state",
        "source",
        "reason",
        "actual_sample_time",
        "actual_age_ms",
        "feedback_quality",
        "feedback_source",
        "last_applied_command_sequence",
        *want,
    }

    d = ResolvedDof(
        dof="rudder",
        source_stream_id="ab" * 16,
        source_sequence=42,
        source_clock_quality=ClockQuality.CLOCK_QUALITY_SYNCHRONISED,
        gate_hold_ms=0,
    )
    d.source_sample_time.FromNanoseconds(1_000_000_000)
    d.source_published_at.FromNanoseconds(1_004_000_000)
    d.gate_received_at.FromNanoseconds(1_060_000_000)
    back = ResolvedDof.FromString(d.SerializeToString())
    assert (
        back.source_sequence == 42
        and back.source_sample_time.ToNanoseconds() == 1_000_000_000
    )
    assert (
        back.HasField("gate_hold_ms") and back.gate_hold_ms == 0
    )  # a zero hold is a reading
    nothing = ResolvedDof.FromString(ResolvedDof(dof="rudder").SerializeToString())
    assert not nothing.HasField("gate_hold_ms") and not nothing.HasField(
        "source_sample_time"
    )
