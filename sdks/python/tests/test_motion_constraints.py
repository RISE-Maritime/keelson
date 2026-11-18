"""Tests for the engineer's veto and its payload twin (protocols/motion_constraints.yaml).

The protocol says who may write which limit and that silence never lifts one;
these pin the parts of that a `.proto` can state but nobody would notice
breaking: that "no limit" and a limit of zero are never the same value, that
both records order by a revision and carry an optional attestation instead of
#358's gate-minted fence, that the key chunks are single strings, and that a
latch names its evidence as a choice of one, never both. Carried over from #358
onto the conn (decision 0001, C4). Design: crowsnest-dev
docs/COMMAND-ARCHITECTURE.md §1.1.
"""

import keelson
import pytest
from google.protobuf.descriptor import FieldDescriptor as F
from keelson import qos

from keelson.payloads.Attestation_pb2 import Attestation
from keelson.payloads.MotionConstraint_pb2 import MotionConstraint
from keelson.payloads.PropulsionConstraint_pb2 import PropulsionConstraint


def _fields(cls):
    return cls.DESCRIPTOR.fields_by_name


# ---------------------------------------------------------------------------
# The subjects.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "subject,type_name",
    [
        ("propulsion_constraint", "keelson.PropulsionConstraint"),
        ("motion_constraint", "keelson.MotionConstraint"),
    ],
)
def test_the_subjects_are_registered_and_travel_elevated(subject, type_name):
    """A constraint that arrives late is a setpoint clamped too late."""
    assert keelson.is_subject_well_known(subject)
    assert keelson.get_subject_schema(subject) == type_name
    assert qos.profile_name_for(subject) == "elevated"


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls,field",
    [
        (PropulsionConstraint, "vessel_id"),
        (MotionConstraint, "vessel_id"),
        (MotionConstraint, "payload_id"),
        (PropulsionConstraint.DeviceConstraint, "device_id"),
    ],
)
def test_key_chunks_are_single_string_fields(cls, field):
    """`{vessel_id}`, `{vessel_id}/{payload_id}`, and a device id that is one key
    chunk on the platform's `propulsion[]` (keelson#326)."""
    fd = _fields(cls)[field]
    assert fd.type == F.TYPE_STRING and not fd.is_repeated


# ---------------------------------------------------------------------------
# Fence + revision: whose authority, and which version of the state.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls,number", [(PropulsionConstraint, 8), (MotionConstraint, 14)]
)
def test_both_order_by_a_revision_and_carry_an_optional_attestation(cls, number):
    """#358's fence was the motion gate's or the payload gate's; the conn has no
    gate to mint one, so a revision orders and an attestation authenticates."""
    from google.protobuf import descriptor_pb2

    assert _fields(cls)["revision"].type == F.TYPE_UINT64
    att = _fields(cls)["attestation"]
    assert (att.number, att.message_type, att.has_presence) == (
        number,
        Attestation.DESCRIPTOR,
        True,
    )
    assert "fence" not in _fields(cls)
    proto = descriptor_pb2.DescriptorProto()
    cls.DESCRIPTOR.CopyToProto(proto)
    assert [(r.start, r.end) for r in proto.reserved_range] == [(5, 6)]
    assert list(proto.reserved_name) == ["fence"]


# ---------------------------------------------------------------------------
# No limit is never zero.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls,field",
    [
        (PropulsionConstraint.DeviceConstraint, "max_power_pct"),
        (MotionConstraint, "max_speed_knots"),
        (MotionConstraint, "max_rate_of_turn_degps"),
        (MotionConstraint, "heading_band"),
        (MotionConstraint, "release"),
    ],
)
def test_every_limit_has_explicit_presence(cls, field):
    assert _fields(cls)[field].has_presence


@pytest.mark.unit
def test_an_unset_power_cap_is_no_cap_and_a_zero_cap_is_a_cap():
    unset = PropulsionConstraint.DeviceConstraint(device_id="main")
    zero = PropulsionConstraint.DeviceConstraint(device_id="main", max_power_pct=0.0)
    back = PropulsionConstraint.DeviceConstraint.FromString(unset.SerializeToString())
    assert not back.HasField("max_power_pct")
    back = PropulsionConstraint.DeviceConstraint.FromString(zero.SerializeToString())
    assert back.HasField("max_power_pct") and back.max_power_pct == 0.0


@pytest.mark.unit
def test_a_derate_round_trips_with_its_writer_and_revision():
    pc = PropulsionConstraint(
        vessel_id="sf18",
        controller_id="eng-1",
        controller_site="ROC-2",
        revision=7,
        devices=[
            PropulsionConstraint.DeviceConstraint(
                device_id="stbd", max_power_pct=60.0, reason="bearing temperature"
            ),
            PropulsionConstraint.DeviceConstraint(device_id="bow-1", inhibit=True),
        ],
    )
    back = PropulsionConstraint.FromString(pc.SerializeToString())
    assert back.revision == 7
    assert [d.device_id for d in back.devices] == ["stbd", "bow-1"]
    assert back.devices[0].max_power_pct == 60.0
    assert back.devices[1].inhibit and not back.devices[1].HasField("max_power_pct")


@pytest.mark.unit
def test_the_rate_of_turn_cap_is_in_degrees_per_second():
    """keelson's yaw-rate unit is deg/s; a deg/min field beside it would be read
    sixty times too permissive."""
    assert "max_rate_of_turn_degps" in _fields(MotionConstraint)
    assert not any(
        n.endswith("_degpm") or n.endswith("degmin") for n in _fields(MotionConstraint)
    )


# ---------------------------------------------------------------------------
# A latched constraint outlives its writer; only matching evidence lifts it.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clears_on_is_exactly_one_kind_of_evidence():
    oneof = MotionConstraint.ClearCondition.DESCRIPTOR.oneofs_by_name["condition"]
    assert sorted(f.name for f in oneof.fields) == ["procedure_id", "sensor"]
    cc = MotionConstraint.ClearCondition(procedure_id="recover-sonar")
    cc.sensor.key_expr = "rise/@v0/sf18/pubsub/winch_stowed/winch"
    assert cc.WhichOneof("condition") == "sensor"  # setting one clears the other


@pytest.mark.unit
def test_a_release_carries_exactly_one_kind_of_evidence_and_who_released_it():
    oneof = MotionConstraint.Release.DESCRIPTOR.oneofs_by_name["evidence"]
    assert sorted(f.name for f in oneof.fields) == ["procedure", "sensor"]
    assert "released_by" in _fields(MotionConstraint.Release)


@pytest.mark.unit
def test_a_latched_sonar_constraint_round_trips_with_its_clear_condition():
    mc = MotionConstraint(
        vessel_id="sf18",
        payload_id="sonar",
        writer_id="payload-gate-sonar",
        revision=1,
        max_speed_knots=4.0,
        max_rate_of_turn_degps=0.5,
        reason="towed body deployed",
        latched=True,
        clears_on=MotionConstraint.ClearCondition(
            sensor=MotionConstraint.SensorEvidence(
                key_expr="rise/@v0/sf18/pubsub/winch_stowed/winch",
                expected_value="true",
            )
        ),
    )
    back = MotionConstraint.FromString(mc.SerializeToString())
    assert back.latched and back.clears_on.WhichOneof("condition") == "sensor"
    assert back.max_speed_knots == 4.0 and not back.HasField("heading_band")
    assert not back.HasField("release")


@pytest.mark.unit
def test_a_release_by_procedure_names_the_person_and_the_procedure():
    mc = MotionConstraint(
        payload_id="sonar",
        revision=2,
        latched=True,
        clears_on=MotionConstraint.ClearCondition(procedure_id="recover-sonar"),
        release=MotionConstraint.Release(
            procedure=MotionConstraint.ProcedureCompletion(
                procedure_id="recover-sonar", operator_id="op-7"
            ),
            released_by="op-7",
        ),
    )
    back = MotionConstraint.FromString(mc.SerializeToString())
    assert back.HasField("release")
    assert back.release.WhichOneof("evidence") == "procedure"
    assert back.release.procedure.procedure_id == back.clears_on.procedure_id


@pytest.mark.unit
def test_the_conn_supervisor_reads_the_constraints():
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "scripts"))
    from protocols_lib import load_all  # noqa: E402

    protocols = {p.name: p for p in load_all(repo / "protocols")}
    mc, conn = protocols["motion_constraints"], protocols["conn"]
    assert mc.data["depends_on"] == ["conn"]
    assert "motion_gate" not in mc.data["roles"]
    assert {"propulsion_constraint", "motion_constraint"} <= set(
        conn.data["roles"]["supervisor"]["reads"]
    )
