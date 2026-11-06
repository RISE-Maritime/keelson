"""The ROC service's attestation on authority_manager/v1 (trust chain B, crowsnest-dev#310): a signed
claim bound to the exact request, fresh by the vessel's own challenge. The manager and the service
live in other repos; this pins the shape they both sign and verify, and the reference procedure.
"""

import hashlib

import pytest

from keelson.interfaces import AuthorityManager_pb2 as pb
from keelson.payloads.CommandAuthority_pb2 import CommandAssignment
from keelson.payloads.ControlMapping_pb2 import ControlMapping

pytestmark = pytest.mark.unit


def digest(request) -> bytes:
    """SHA-256 of the request serialized deterministically, with caller.attestation cleared."""
    bare = type(request)()
    bare.CopyFrom(request)
    bare.caller.ClearField("attestation")
    return hashlib.sha256(bare.SerializeToString(deterministic=True)).digest()


def take_control(reason="manoeuvre", rows=("rudder", "throttle")):
    return pb.TakeControlRequest(
        caller=pb.Caller(
            controller_id="ted@ROC-1",
            controller_site="ROC-1",
            token="s1",
            assignment_id="a1",
            assignment_revision=3,
        ),
        mappings=[
            ControlMapping(dof=d, execution_source=f"ROC-1/helm-1/{d}") for d in rows
        ],
        reason=reason,
    )


def claim_for(request, procedure="take_control", sequence=7, challenge=b"\x01" * 16):
    return pb.AttestationClaim(
        service_id="roc1-authority",
        key_id="roc1-2026a",
        operator_id="ted@ROC-1",
        site="ROC-1",
        station="ROC-1/console-2",
        session_id="sess-9",
        sequence=sequence,
        challenge=challenge,
        procedure=procedure,
        request_sha256=digest(request),
    )


# ---------------------------------------------------------------------------- the shape


def test_the_caller_carries_an_optional_attestation_of_claim_bytes_and_a_signature():
    f = pb.Caller.DESCRIPTOR.fields_by_name["attestation"]
    assert (f.number, f.message_type.full_name, f.has_presence) == (
        6,
        "keelson.interfaces.authority_manager.Attestation",
        True,
    )
    assert not pb.Caller().HasField("attestation")
    a = pb.Attestation.DESCRIPTOR.fields_by_name
    assert (a["claim"].type, a["signature"].type) == (
        a["claim"].TYPE_BYTES,
        a["signature"].TYPE_BYTES,
    )


def test_the_claim_names_who_which_session_which_challenge_and_which_call():
    names = set(pb.AttestationClaim.DESCRIPTOR.fields_by_name)
    assert {
        "service_id",
        "key_id",
        "operator_id",
        "site",
        "station",
        "session_id",
        "sequence",
        "challenge",
        "procedure",
        "request_sha256",
        "issued_at",
    } == names
    req = take_control()
    back = pb.AttestationClaim.FromString(claim_for(req).SerializeToString())
    assert back.request_sha256 == digest(req) and back.sequence == 7


def test_the_refusal_and_the_challenge_exist():
    assert pb.Refusal.Value("REFUSAL_ATTESTATION") == 13
    f = CommandAssignment.DESCRIPTOR.fields_by_name["attestation_challenge"]
    assert (f.number, f.type) == (8, f.TYPE_BYTES)
    s = pb.AuthorityState.DESCRIPTOR.fields_by_name["attestation_challenges"]
    assert (s.number, s.is_repeated) == (10, True)


# ---------------------------------------------------------------------------- the request digest


def test_the_digest_is_stable_and_ignores_the_attestation_itself():
    req = take_control()
    again = take_control()
    assert digest(req) == digest(again)
    req.caller.attestation.claim = b"anything"
    req.caller.attestation.signature = b"anything"
    assert digest(req) == digest(again)  # the attestation is cleared before hashing


@pytest.mark.parametrize(
    "change",
    [
        lambda r: setattr(r, "reason", "another"),
        lambda r: r.mappings[0].__setattr__("execution_source", "ROC-2/helm-1/rudder"),
        lambda r: r.mappings.add(
            dof="bow_thruster", execution_source="ROC-1/helm-1/bow_thruster"
        ),
        lambda r: setattr(r.caller, "assignment_revision", 4),
        lambda r: setattr(r.caller, "controller_id", "anna@ROC-1"),
    ],
)
def test_any_change_to_the_call_changes_the_digest(change):
    req = take_control()
    before = digest(req)
    change(req)
    assert digest(req) != before


def test_another_procedures_request_never_shares_a_digest():
    abort = pb.AbortAutomationRequest(caller=take_control().caller, reason="manoeuvre")
    assert digest(abort) != digest(take_control())


# ---------------------------------------------------------------------------- the reference sign / verify


def test_a_signed_claim_verifies_and_every_tampering_fails():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.exceptions import InvalidSignature

    key, other = ed.Ed25519PrivateKey.generate(), ed.Ed25519PrivateKey.generate()
    public = key.public_key()
    req = take_control()
    claim = claim_for(req).SerializeToString()
    req.caller.attestation.claim = claim
    req.caller.attestation.signature = key.sign(claim)

    def verifies(request, pub=public) -> bool:
        att = request.caller.attestation
        try:
            pub.verify(att.signature, att.claim)
        except InvalidSignature:
            return False
        c = pb.AttestationClaim.FromString(att.claim)
        return (
            c.request_sha256 == digest(request)
            and c.operator_id == request.caller.controller_id
            and c.site == request.caller.controller_site
        )

    assert verifies(req)
    assert not verifies(req, other.public_key())  # another service's key
    tampered = bytearray(claim)
    tampered[-1] ^= 1
    req2 = take_control()
    req2.caller.attestation.claim = bytes(tampered)
    req2.caller.attestation.signature = req.caller.attestation.signature
    assert not verifies(req2)  # one claim byte changed
    lifted = take_control(reason="another")  # the same attestation on a different call
    lifted.caller.attestation.CopyFrom(req.caller.attestation)
    assert not verifies(lifted)
    relabelled = take_control()
    relabelled.caller.attestation.CopyFrom(req.caller.attestation)
    relabelled.caller.controller_id = (
        "anna@ROC-1"  # a label that disagrees with the claim
    )
    assert not verifies(relabelled)
