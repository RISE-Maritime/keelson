"""Human roles as attested claims (carry-over C3 of docs/decisions/0001-coxswain-conn.md).

The vessel keeps no role registry: the authority service ashore attests the
roles an operator holds inside the signed claim, and the receiver checks it
against what its manifest or policy requires.
"""

from __future__ import annotations

import hashlib

import pytest
from google.protobuf.descriptor import FieldDescriptor as F

from keelson.interfaces.Conn_pb2 import ConnReply, ConnRequest
from keelson.payloads.Attestation_pb2 import (
    Attestation,
    AttestationClaim,
    Role,
    RoleGrant,
)
from keelson.payloads.NavigationState_pb2 import NavigationState
from keelson.payloads.SafetyState_pb2 import (
    EmergencyCommand,
    EmergencyRequest,
    SafetyRequestOutcome,
)

pytestmark = pytest.mark.unit


def test_roles_keep_331s_numbering_and_the_conn_is_not_a_role():
    from google.protobuf import descriptor_pb2

    assert {name: Role.Value(name) for name in Role.keys()} == {
        "ROLE_UNSPECIFIED": 0,
        "ROLE_OVERALL_COMMAND": 2,
        "ROLE_NAVIGATIONAL_COMMAND": 3,
        "ROLE_NAVIGATION_AUTHORITY": 4,
        "ROLE_ENGINEER": 5,
        "ROLE_PAYLOAD_AUTHORITY": 6,
    }
    proto = descriptor_pb2.EnumDescriptorProto()
    Role.DESCRIPTOR.CopyToProto(proto)
    assert [(r.start, r.end) for r in proto.reserved_range] == [(1, 1)]


def test_a_claim_grants_scoped_roles():
    claim = AttestationClaim(
        operator_id="anna@ROC-1",
        roles=[
            RoleGrant(role=Role.ROLE_NAVIGATIONAL_COMMAND),
            RoleGrant(role=Role.ROLE_PAYLOAD_AUTHORITY, scope="towed-sonar"),
        ],
    )
    back = AttestationClaim.FromString(claim.SerializeToString())
    assert [(g.role, g.scope) for g in back.roles] == [
        (Role.ROLE_NAVIGATIONAL_COMMAND, ""),
        (Role.ROLE_PAYLOAD_AUTHORITY, "towed-sonar"),
    ]


def test_the_attestation_is_one_shared_type_for_calls_and_requests():
    conn = ConnRequest.DESCRIPTOR.fields_by_name["attestation"]
    emergency = EmergencyRequest.DESCRIPTOR.fields_by_name["attestation"]
    assert conn.message_type is emergency.message_type is Attestation.DESCRIPTOR
    assert (emergency.number, emergency.has_presence) == (14, True)


def test_the_refusals_for_a_missing_role_or_attestation():
    assert ConnReply.Result.Value("REFUSED_ROLE") == 13
    assert SafetyRequestOutcome.Value("SAFETY_REQUEST_OUTCOME_REFUSED_ATTESTATION") == 9


def _digest(message) -> bytes:
    bare = type(message)()
    bare.CopyFrom(message)
    bare.ClearField("attestation")
    return hashlib.sha256(bare.SerializeToString(deterministic=True)).digest()


def test_a_reset_claim_binds_to_its_request_and_its_fence():
    reset = EmergencyRequest(
        vessel_id="alpha",
        request_id="r-1",
        command=EmergencyCommand.EMERGENCY_COMMAND_RESET,
        controller_id="anna@ROC-1",
        controller_site="ROC-1",
        claimant_id=3,
    )
    reset.expected_stopped_fence.term = "t"
    reset.expected_stopped_fence.generation = 4
    claim = AttestationClaim(
        operator_id="anna@ROC-1",
        site="ROC-1",
        procedure="emergency_request",
        request_sha256=_digest(reset),
        roles=[RoleGrant(role=Role.ROLE_OVERALL_COMMAND)],
    )
    reset.attestation.CopyFrom(Attestation(claim=claim.SerializeToString()))
    assert _digest(reset) == claim.request_sha256
    # The same claim does not cover a RESET against another fence.
    other = EmergencyRequest()
    other.CopyFrom(reset)
    other.expected_stopped_fence.generation = 5
    assert _digest(other) != claim.request_sha256


def test_navigation_state_names_the_human_decision_in_force():
    fields = NavigationState.DESCRIPTOR.fields_by_name
    assert (fields["order_id"].number, fields["order_id"].has_presence) == (14, True)
    assert (fields["ordered_by"].number, fields["ordered_by"].type) == (
        15,
        F.TYPE_STRING,
    )
    assert not NavigationState().HasField("order_id")  # absent: no human decision
