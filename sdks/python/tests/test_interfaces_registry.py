"""Unit tests for keelson.interfaces runtime introspection (#130 addendum)."""

from unittest.mock import Mock

import pytest

import keelson
from keelson.interfaces import (
    RpcError,
    get_interface_descriptor,
    get_interfaces_file_descriptor_set,
    get_procedure_message_classes,
    get_procedure_schemas,
    get_procedures,
    invoke_procedure,
    list_interfaces,
)
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse


@pytest.mark.unit
def test_list_interfaces_matches_bundled_registry():
    interfaces = list_interfaces()
    assert ("vehicle_lifecycle", "v1") in interfaces
    assert ("replay_control", "v1") in interfaces
    assert ("configurable", "v1") in interfaces
    # Every listed (interface, version) is resolvable to a service.
    for interface, version in interfaces:
        service = get_interface_descriptor(interface, version)
        assert service.full_name == keelson.get_interface_service(
            f"{interface}/{version}"
        )
        assert get_procedures(interface, version)


@pytest.mark.unit
def test_get_procedures_and_schemas():
    assert get_procedures("vehicle_lifecycle", "v1") == [
        "arm",
        "set_mode",
        "emergency_stop",
    ]
    req, resp = get_procedure_schemas("vehicle_mission", "v1", "upload_mission")
    assert req.full_name == "keelson.Mission"  # shared domain type (#153)
    assert resp.full_name == (
        "keelson.interfaces.vehicle_mission.MissionUploadResponse"
    )


@pytest.mark.unit
def test_message_classes_roundtrip():
    ReqCls, _ = get_procedure_message_classes("replay_control", "v1", "set_speed")
    msg = ReqCls(speed=2.5)
    decoded = ReqCls.FromString(msg.SerializeToString())
    assert decoded.speed == 2.5


@pytest.mark.unit
def test_descriptor_set_bytes_cover_domain_imports():
    from google.protobuf.descriptor_pb2 import FileDescriptorSet

    fds = FileDescriptorSet.FromString(get_interfaces_file_descriptor_set())
    names = {f.name for f in fds.file}
    assert "VehicleMission.proto" in names
    assert "Mission.proto" in names  # --include_imports pulls the domain pool in
    assert "Coordinate.proto" in names


def _reply_ok(payload_bytes: bytes):
    reply = Mock()
    reply.ok.payload.to_bytes = Mock(return_value=payload_bytes)
    return reply


def _reply_err(payload_bytes: bytes):
    reply = Mock()
    reply.ok = None
    reply.err.payload.to_bytes = Mock(return_value=payload_bytes)
    return reply


@pytest.mark.unit
def test_invoke_procedure_decodes_ok_reply():
    _, RespCls = get_procedure_message_classes("replay_control", "v1", "play")
    session = Mock()
    session.get = Mock(return_value=iter([_reply_ok(RespCls().SerializeToString())]))

    response = invoke_procedure(
        session,
        "realm",
        "boat",
        "replay_control",
        "v1",
        "play",
        "mcap/0",
    )
    assert response.DESCRIPTOR.full_name == RespCls.DESCRIPTOR.full_name

    key = session.get.call_args.args[0]
    assert key == "realm/@v0/boat/@rpc/replay_control/v1/play/mcap/0"
    assert session.get.call_args.kwargs["timeout"] == 10.0


@pytest.mark.unit
def test_invoke_procedure_raises_typed_rpc_error():
    err = ErrorResponse(
        error_description="no file loaded", code=ErrorResponse.Code.INVALID_STATE
    )
    session = Mock()
    session.get = Mock(return_value=iter([_reply_err(err.SerializeToString())]))

    with pytest.raises(RpcError) as excinfo:
        invoke_procedure(
            session, "realm", "boat", "replay_control", "v1", "seek", "mcap/0"
        )
    assert excinfo.value.code_name == "INVALID_STATE"
    assert "no file loaded" in excinfo.value.description


@pytest.mark.unit
def test_invoke_procedure_times_out_on_no_reply():
    session = Mock()
    session.get = Mock(return_value=iter([]))
    with pytest.raises(TimeoutError):
        invoke_procedure(
            session, "realm", "boat", "replay_control", "v1", "play", "mcap/0"
        )
