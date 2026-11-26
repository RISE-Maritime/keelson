"""Tests for the command architecture's authority types (protocols/command_authority.yaml).

The protocol says who may do what; these pin the parts of that a `.proto` can
state but nobody would notice breaking: that a record with no role is still the
conn, that the two halves of a fence are one shared type, that the navigation
authority document carries no fence and no TTL, that the authority holder and
the execution source are two fields, and that a transfer proposal carries a
duration and never a fence. Design: crowsnest-dev docs/COMMAND-ARCHITECTURE.md.
"""

import keelson
import pytest
from google.protobuf.descriptor import FieldDescriptor as F

from keelson.payloads.CommandAuthority_pb2 import (
    Assignment,
    CommandAuthority,
    CommandRequest,
    RecordKind,
    RequestKind,
    Role,
)
from keelson.payloads.ControlMapping_pb2 import (
    ControlMapping,
    TransferAck,
    TransferCommit,
    TransferKind,
    TransferPrepare,
)
from keelson.payloads.Fence_pb2 import Fence
from keelson.payloads.GuidanceCandidate_pb2 import CandidateStream, GuidanceCandidate
from keelson.payloads.NavigationAuthority_pb2 import (
    CommsLossBehaviour,
    ControlMethod,
    FunctionAuthority,
    FunctionEnvelope,
    NavigationAuthority,
    NavigationFunction,
)


def _fields(cls):
    return cls.DESCRIPTOR.fields_by_name


# ---------------------------------------------------------------------------
# The subjects are unchanged: roles ride the existing slot family.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_subjects_are_the_ones_every_station_already_reads():
    assert keelson.get_subject_schema("command_authority") == "keelson.CommandAuthority"
    assert keelson.get_subject_schema("command_request") == "keelson.CommandRequest"


@pytest.mark.unit
def test_key_variables_are_single_string_fields():
    """The key templates are `{vessel_id}`, `{vessel_id}/payload/{payload_id}`, …
    — a variable that is not a plain string field cannot be a key chunk."""
    for cls in (CommandAuthority, CommandRequest):
        for name in ("vessel_id", "payload_id"):
            fd = _fields(cls)[name]
            assert fd.type == F.TYPE_STRING and not fd.is_repeated, (
                cls.__name__,
                name,
            )


# ---------------------------------------------------------------------------
# Migration: a record written before roles existed keeps its meaning.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_role_zero_is_unspecified_and_the_conn_is_one():
    """keelson's convention holds: the proto default is never a state. A reader
    treats ROLE_UNSPECIFIED as ROLE_CONN (legacy leases carry no role); a new
    writer always sets the role, so ROLE_CONN is spelled on the wire."""
    assert Role.ROLE_UNSPECIFIED == 0
    assert Role.ROLE_CONN == 1
    assert Role.keys() == [
        "ROLE_UNSPECIFIED",
        "ROLE_CONN",
        "ROLE_OVERALL_COMMAND",
        "ROLE_NAVIGATIONAL_COMMAND",
        "ROLE_NAVIGATION_AUTHORITY",
        "ROLE_ENGINEER",
        "ROLE_PAYLOAD_AUTHORITY",
    ]
    # Maintenance is a procedure with a state machine, not a role.
    assert not any("MAINTENANCE" in k for k in Role.keys())


@pytest.mark.unit
def test_an_old_lease_decodes_as_a_conn_lease():
    old = CommandAuthority(
        vessel_id="sf18",
        controller_id="ted@ROC-1",
        controller_site="ROC-1",
        token="ab" * 16,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=10,
    )
    decoded = CommandAuthority.FromString(old.SerializeToString())
    # A legacy lease carries no role; a reader treats UNSPECIFIED as the conn.
    assert decoded.role == Role.ROLE_UNSPECIFIED
    assert decoded.kind == RecordKind.RECORD_KIND_UNSPECIFIED  # reads as LEASE
    assert not decoded.HasField("assignment")
    assert not decoded.HasField("fence")
    assert not decoded.HasField("navigation_authority")


@pytest.mark.unit
def test_record_kind_and_request_kind_keep_the_default_sentinel():
    assert RecordKind.RECORD_KIND_UNSPECIFIED == 0
    assert RecordKind.keys() == [
        "RECORD_KIND_UNSPECIFIED",
        "RECORD_KIND_LEASE",
        "RECORD_KIND_ASSIGNMENT",
        "RECORD_KIND_SESSION",
    ]
    assert RequestKind.REQUEST_KIND_UNSPECIFIED == 0
    assert set(RequestKind.keys()) >= {
        "REQUEST_KIND_REQUEST",
        "REQUEST_KIND_TAKE_CONTROL",
        "REQUEST_KIND_ABORT_AUTOMATION",
        "REQUEST_KIND_ASSUME_COMMAND",
    }


# ---------------------------------------------------------------------------
# Assignment vs session: responsibility never expires by silence.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_record_carries_the_roles_fields_with_presence_where_absence_means_something():
    fields = _fields(CommandAuthority)
    for name in (
        "role",
        "kind",
        "payload_id",
        "issued_by",
        "assignment",
        "fence",
        "responsible_roc",
        "active_control_station",
        "navigation_authority",
    ):
        assert name in fields, name
    # Absent is a meaning for each of these, so each must be observable as absent.
    for name in ("assignment", "fence", "navigation_authority"):
        assert fields[name].has_presence, name


@pytest.mark.unit
def test_an_assignment_has_a_revision_and_marks_a_synthesised_one():
    fields = _fields(Assignment)
    assert fields["revision"].type == F.TYPE_UINT64
    assert fields["synthesised"].type == F.TYPE_BOOL
    assert fields["succession"].has_presence
    assert _fields(CommandRequest)["assignment_revision"].type == F.TYPE_UINT64


@pytest.mark.unit
def test_a_session_round_trips_the_assignment_it_belongs_to():
    session = CommandAuthority(
        vessel_id="sf18",
        controller_id="master@ROC-1",
        role=Role.ROLE_OVERALL_COMMAND,
        kind=RecordKind.RECORD_KIND_SESSION,
        token="cd" * 16,
        lease_ttl_seconds=30,
        assignment=Assignment(
            assignee="master@ROC-1", assignment_id="ef" * 16, revision=3
        ),
    )
    decoded = CommandAuthority.FromString(session.SerializeToString())
    assert decoded.assignment.revision == 3
    assert decoded.kind == RecordKind.RECORD_KIND_SESSION


# ---------------------------------------------------------------------------
# Fence: one shared type, two owners of its halves.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_fence_is_a_term_and_a_generation():
    fields = _fields(Fence)
    assert list(fields) == ["term", "generation"]
    assert fields["term"].type == F.TYPE_STRING
    assert fields["generation"].type == F.TYPE_UINT64


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls, field",
    [
        (CommandAuthority, "fence"),
        (ControlMapping, "fence"),
        (TransferAck, "proposed_fence"),
        (TransferCommit, "fence"),
    ],
)
def test_every_fence_field_is_the_same_type(cls, field):
    assert _fields(cls)[field].message_type.full_name == "keelson.Fence"


# ---------------------------------------------------------------------------
# Navigation authority: per function, no fence, no TTL, no summary level.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_navigation_authority_is_an_assignment_document_not_a_lease():
    fields = _fields(NavigationAuthority)
    assert set(fields) == {"assignment_id", "revision", "functions"}
    for forbidden in (
        "fence",
        "holder",
        "token",
        "lease_ttl_seconds",
        "expires_at",
        "navigation_level",
    ):
        assert forbidden not in fields, forbidden
    assert fields["functions"].is_repeated
    assert fields["functions"].message_type.full_name == "keelson.FunctionAuthority"


@pytest.mark.unit
def test_a_function_binds_its_holder_to_the_lease_that_proves_them():
    fields = _fields(FunctionAuthority)
    assert set(fields) >= {
        "function",
        "holder",
        "holder_lease_id",
        "control_method",
        "envelope",
    }
    env = _fields(FunctionEnvelope)
    assert set(env) >= {
        "intervention_window_ms",
        "intent_delivery_required",
        "comms_loss_behaviour",
    }


@pytest.mark.unit
def test_the_six_navigation_functions():
    assert NavigationFunction.keys() == [
        "NAVIGATION_FUNCTION_UNSPECIFIED",
        "NAVIGATION_FUNCTION_ROUTE_EXECUTION",
        "NAVIGATION_FUNCTION_TRACK_KEEPING",
        "NAVIGATION_FUNCTION_SPEED_CONTROL",
        "NAVIGATION_FUNCTION_COLLISION_AVOIDANCE",
        "NAVIGATION_FUNCTION_ROUTE_MODIFICATION",
        "NAVIGATION_FUNCTION_EMERGENCY_AVOIDANCE",
    ]


@pytest.mark.unit
def test_control_methods_are_numbered_as_sarums_numbers_them():
    """The authority divisor lies between 2 and 3: at DELEGATED and above the
    navigator holds the function."""
    assert ControlMethod.CONTROL_METHOD_OPERATED == 1
    assert ControlMethod.CONTROL_METHOD_DIRECTED == 2
    assert ControlMethod.CONTROL_METHOD_DELEGATED == 3
    assert ControlMethod.CONTROL_METHOD_MONITORED == 4
    assert ControlMethod.CONTROL_METHOD_AUTONOMOUS == 5
    assert CommsLossBehaviour.keys() == [
        "COMMS_LOSS_BEHAVIOUR_UNSPECIFIED",
        "COMMS_LOSS_BEHAVIOUR_CONTINUE",
        "COMMS_LOSS_BEHAVIOUR_HOLD",
        "COMMS_LOSS_BEHAVIOUR_FALLBACK_TO_METHOD_2",
        "COMMS_LOSS_BEHAVIOUR_SAFE_STATE",
    ]


# ---------------------------------------------------------------------------
# The mapping and the transfer: holder ≠ source; the gate mints the fence.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_mapping_names_the_holder_and_the_source_separately():
    fields = _fields(ControlMapping)
    assert set(fields) >= {
        "dof",
        "authority_holder",
        "authority_lease_id",
        "execution_source",
        "fence",
        "execution_envelope",
    }


@pytest.mark.unit
def test_a_prepare_carries_a_duration_and_never_a_fence_or_an_absolute_expiry():
    fields = _fields(TransferPrepare)
    assert fields["prepare_ttl_ms"].type == F.TYPE_UINT32
    assert "fence" not in fields
    assert not any(
        token in name for name in fields for token in ("expires", "deadline", "_at")
    ), list(fields)
    assert _fields(TransferAck)["proposed_fence"].has_presence
    assert TransferKind.keys() == [
        "TRANSFER_KIND_UNSPECIFIED",
        "TRANSFER_KIND_GRANT",
        "TRANSFER_KIND_TAKE_CONTROL",
        "TRANSFER_KIND_STATION_HANDOVER",
        "TRANSFER_KIND_ABORT_AUTOMATION",
    ]


# ---------------------------------------------------------------------------
# Candidates: decision basis ⊥ execution basis; ordering per stream.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_decision_basis_and_execution_basis_are_two_independent_choices():
    oneofs = {
        o.name: [f.name for f in o.fields] for o in GuidanceCandidate.DESCRIPTOR.oneofs
    }
    assert set(oneofs["decision_basis"]) == {"nav_function", "human_order"}
    assert set(oneofs["execution_basis"]) == {"direct", "carry_out"}


@pytest.mark.unit
def test_a_candidate_carries_a_stream_id_a_sequence_and_a_clock_quality():
    assert set(_fields(CandidateStream)) == {"stream_id", "sequence", "clock_quality"}
    assert _fields(CandidateStream)["sequence"].type == F.TYPE_UINT64
    assert _fields(GuidanceCandidate)["stream"].message_type.full_name == (
        "keelson.CandidateStream"
    )


@pytest.mark.unit
def test_a_new_conn_lease_spells_its_role():
    lease = CommandAuthority(vessel_id="sf18", role=Role.ROLE_CONN)
    assert CommandAuthority.FromString(lease.SerializeToString()).role == 1


# ---------------------------------------------------------------------------
# command_assignment: the manager's projection of the human assignments.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_command_assignment_is_a_registered_subject_travelling_elevated():
    from keelson import qos

    assert keelson.is_subject_well_known("command_assignment")
    assert keelson.get_subject_schema("command_assignment") == (
        "keelson.CommandAssignment"
    )
    assert qos.profile_name_for("command_assignment") == "elevated"


@pytest.mark.unit
def test_command_assignment_carries_both_assignments_and_its_issuer():
    from keelson.payloads.CommandAuthority_pb2 import CommandAssignment

    fields = _fields(CommandAssignment)
    assert set(fields) == {
        "vessel_id",
        "overall",
        "navigational",
        "issuer_id",
        "issued_at",
        "heartbeat_interval_seconds",
        "feed_ttl_seconds",
    }
    for name in ("overall", "navigational"):
        assert fields[name].message_type.full_name == "keelson.Assignment"
        assert fields[name].has_presence  # absent = nobody assigned
    assert {"assignee", "assignment_id", "revision"} <= set(_fields(Assignment))
    # Liveness is a duration armed on the reader's clock, never an expiry time.
    assert fields["feed_ttl_seconds"].type == F.TYPE_UINT32
    assert not any("expire" in n for n in fields)
