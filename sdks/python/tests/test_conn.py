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


def test_conn_request_reserves_field_2_for_attestation():
    from google.protobuf import descriptor_pb2
    from keelson.interfaces.Conn_pb2 import ConnRequest

    proto = descriptor_pb2.DescriptorProto()
    ConnRequest.DESCRIPTOR.CopyToProto(proto)
    assert [f.number for f in proto.field] == [1]
    assert [(r.start, r.end) for r in proto.reserved_range] == [(2, 3)]


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
