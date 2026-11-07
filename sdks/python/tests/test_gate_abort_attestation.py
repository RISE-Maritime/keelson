"""The direct abort is attested too (crowsnest-dev#316): control_gate/v1 abort_automation carries the
same authority_manager/v1 Attestation, verified by the gate against a challenge the gate itself
publishes on resolved_control -- because the manager may be the thing that is down.
"""

import hashlib

import pytest

from keelson.interfaces import AuthorityManager_pb2 as am
from keelson.interfaces import ControlGate_pb2 as gate
from keelson.payloads.ResolvedControl_pb2 import ResolvedControl

pytestmark = pytest.mark.unit

PROCEDURE = "control_gate/v1/abort_automation"


def digest(request) -> bytes:
    """SHA-256 of the request serialized deterministically, with `attestation` cleared."""
    bare = type(request)()
    bare.CopyFrom(request)
    bare.ClearField("attestation")
    return hashlib.sha256(bare.SerializeToString(deterministic=True)).digest()


def abort(reason="automation heading for the breakwater"):
    return gate.AbortAutomationRequest(
        requester_id="ted@ROC-1",
        requester_site="ROC-1",
        assignment_id="a1",
        assignment_revision=3,
        session_token="s1",
        reason=reason,
    )


def test_the_direct_abort_carries_an_optional_attestation_of_the_managers_type():
    f = gate.AbortAutomationRequest.DESCRIPTOR.fields_by_name["attestation"]
    assert f.number == 8
    assert f.has_presence  # absent is not an empty claim
    assert f.message_type is am.Attestation.DESCRIPTOR  # ONE claim format, one signer


def test_the_request_otherwise_keeps_its_fields():
    names = set(gate.AbortAutomationRequest.DESCRIPTOR.fields_by_name)
    assert names == {
        "timestamp",
        "requester_id",
        "requester_site",
        "assignment_id",
        "assignment_revision",
        "session_token",
        "reason",
        "attestation",
    }


def test_the_gate_publishes_its_challenge_on_resolved_control():
    f = ResolvedControl.DESCRIPTOR.fields_by_name["attestation_challenge"]
    assert f.number == 12
    assert f.type == f.TYPE_BYTES
    assert ResolvedControl().attestation_challenge == b""  # a gate that does not verify


def test_the_digest_ignores_the_attestation_and_binds_every_other_field():
    req = abort()
    before = digest(req)
    req.attestation.claim = b"anything"
    req.attestation.signature = b"anything"
    assert digest(req) == before
    for change in (
        lambda r: setattr(r, "reason", "another"),
        lambda r: setattr(r, "assignment_revision", 4),
        lambda r: setattr(r, "requester_id", "anna@ROC-1"),
        lambda r: setattr(r, "session_token", "s2"),
    ):
        other = abort()
        change(other)
        assert digest(other) != before


def test_a_managers_abort_never_shares_the_gates_digest():
    managers = am.AbortAutomationRequest(
        caller=am.Caller(controller_id="ted@ROC-1", controller_site="ROC-1"),
        reason="automation heading for the breakwater",
    )
    bare = am.AbortAutomationRequest()
    bare.CopyFrom(managers)
    bare.caller.ClearField("attestation")
    theirs = hashlib.sha256(bare.SerializeToString(deterministic=True)).digest()
    assert theirs != digest(abort())


def test_a_signed_claim_for_the_gate_verifies_and_one_for_the_manager_is_another_call():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    key = ed.Ed25519PrivateKey.generate()
    req = abort()
    claim = am.AttestationClaim(
        service_id="roc1-authority",
        key_id="roc1-2026a",
        operator_id=req.requester_id,
        site=req.requester_site,
        session_id="sess-9",
        sequence=1,
        challenge=b"\x02" * 16,
        procedure=PROCEDURE,
        request_sha256=digest(req),
    ).SerializeToString()
    req.attestation.claim = claim
    req.attestation.signature = key.sign(claim)
    back = gate.AbortAutomationRequest.FromString(req.SerializeToString())
    key.public_key().verify(back.attestation.signature, back.attestation.claim)
    parsed = am.AttestationClaim.FromString(back.attestation.claim)
    assert parsed.procedure == PROCEDURE != "abort_automation"
    assert parsed.request_sha256 == digest(back)
