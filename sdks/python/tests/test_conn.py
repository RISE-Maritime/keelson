"""The conn — conn/v1 and its subjects, as upstreamed from coxswain.

See docs/decisions/0001-coxswain-conn.md and protocols/conn.yaml.
"""

from __future__ import annotations

import keelson
from keelson import interfaces


def test_conn_v1_is_registered_with_coxswains_procedures():
    assert keelson.is_interface_well_known("conn/v1")
    assert keelson.get_interface_service("conn/v1") == "keelson.interfaces.conn.Conn"
    assert sorted(interfaces.get_procedures("conn", "v1")) == [
        "arm",
        "disarm",
        "register",
        "release",
        "request",
    ]


def test_every_procedure_takes_a_conn_request_and_replies_a_conn_reply():
    for procedure in ("register", "request", "release", "arm", "disarm"):
        request_cls, reply_cls = interfaces.get_procedure_message_classes(
            "conn", "v1", procedure
        )
        assert request_cls.DESCRIPTOR.full_name == "keelson.interfaces.conn.ConnRequest"
        assert reply_cls.DESCRIPTOR.full_name == "keelson.interfaces.conn.ConnReply"


def test_conn_reply_results_keep_coxswains_numbering():
    from keelson.interfaces.Conn_pb2 import ConnReply

    # Wire-compatible with coxswain's ConnReply.Result: same names, same numbers.
    expected = [
        "OK",
        "ALREADY_REGISTERED",
        "REGISTRY_FULL",
        "CONN_HELD",
        "NOT_HOLDER",
        "UNREGISTERED",
        "REFUSED_ESTIMATOR",
        "REFUSED_VOLTAGE",
        "REFUSED_POSITION",
        "ERROR",
        "REFUSED_POWER_STALE",
    ]
    assert [ConnReply.Result.Name(i) for i in range(len(expected))] == expected


def test_conn_request_carries_an_optional_attestation_in_field_2():
    from keelson.interfaces.Conn_pb2 import Attestation, ConnRequest

    f = ConnRequest.DESCRIPTOR.fields_by_name["attestation"]
    assert (f.number, f.message_type.full_name, f.has_presence) == (
        2,
        "keelson.interfaces.conn.Attestation",
        True,
    )
    assert not ConnRequest(claimant_id=3).HasField("attestation")
    assert set(Attestation.DESCRIPTOR.fields_by_name) == {"claim", "signature"}


def test_attestation_claim_keeps_the_fields_of_383():
    from keelson.interfaces.Conn_pb2 import AttestationClaim

    assert set(AttestationClaim.DESCRIPTOR.fields_by_name) == {
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
    }


def test_the_supervisor_publishes_the_challenge_on_conn_state():
    from keelson.interfaces.Conn_pb2 import ConnReply
    from keelson.payloads.ConnState_pb2 import ConnState

    f = ConnState.DESCRIPTOR.fields_by_name["attestation_challenge"]
    assert (f.number, f.type) == (4, f.TYPE_BYTES)
    assert ConnReply.Result.Value("REFUSED_ATTESTATION") == 11


def _digest(request) -> bytes:
    """The reference request digest: deterministic, with `attestation` cleared."""
    import hashlib

    bare = type(request)()
    bare.CopyFrom(request)
    bare.ClearField("attestation")
    return hashlib.sha256(bare.SerializeToString(deterministic=True)).digest()


def test_the_request_digest_binds_the_claim_to_one_claimant_and_ignores_itself():
    from keelson.interfaces.Conn_pb2 import Attestation, AttestationClaim, ConnRequest

    request = ConnRequest(claimant_id=3)
    claim = AttestationClaim(
        service_id="roc1-authority",
        key_id="roc1-2026a",
        operator_id="ted@ROC-1",
        site="ROC-1",
        station="ROC-1/console-2",
        session_id="sess-9",
        sequence=7,
        challenge=b"\x01" * 16,
        procedure="request",
        request_sha256=_digest(request),
    )
    signed = ConnRequest(claimant_id=3)
    signed.attestation.CopyFrom(
        Attestation(claim=claim.SerializeToString(), signature=b"\x00" * 64)
    )

    # Attaching the attestation does not change the digest it signs...
    assert _digest(signed) == claim.request_sha256
    # ...but the same claim lifted onto another claimant's request does not match.
    assert _digest(ConnRequest(claimant_id=4)) != claim.request_sha256
    # The verifier parses the signed bytes; it never re-serializes the claim.
    assert AttestationClaim.FromString(signed.attestation.claim) == claim


def test_conn_subjects_resolve_and_round_trip_through_an_envelope():
    from keelson.payloads.ConnState_pb2 import ConnState, Manifest, Setpoint

    assert keelson.get_subject_schema("setpoint") == "keelson.Setpoint"
    assert keelson.get_subject_schema("conn_state") == "keelson.ConnState"
    assert keelson.get_subject_schema("manifest") == "keelson.Manifest"

    sp = Setpoint()
    sp.follow_path.speed_mps = 2.5
    wp = sp.follow_path.waypoints.add()
    wp.latitude_deg, wp.longitude_deg = 57.7, 11.9
    samples = {
        "setpoint": sp,
        "conn_state": ConnState(held=True, holder=1, armed=True),
        "manifest": Manifest(sha256=b"\x01" * 32, revision=7),
    }
    for subject, message in samples.items():
        _, _, payload = keelson.uncover(keelson.enclose(message.SerializeToString()))
        decoded = keelson.decode_protobuf_payload_from_type_name(
            payload, keelson.get_subject_schema(subject)
        )
        # The registry decodes into its own pool's class, so compare the wire form.
        assert decoded.SerializeToString() == message.SerializeToString(), subject

    assert sp.WhichOneof("setpoint") == "follow_path"


def test_setpoint_variants_keep_coxswains_field_numbers():
    from keelson.payloads.ConnState_pb2 import Setpoint

    oneof = Setpoint.DESCRIPTOR.oneofs_by_name["setpoint"]
    assert {f.name: f.number for f in oneof.fields} == {
        "idle": 2,
        "heading_speed": 3,
        "station_keep": 4,
        "follow_path": 5,
        "direct_effort": 6,
    }
