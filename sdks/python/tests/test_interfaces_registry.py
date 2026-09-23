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
    # Declaration order is load-bearing: crowsnest's
    # scripts/checks/containerControl.mjs pins the same list.
    assert get_procedures("container_control", "v1") == [
        "list",
        "logs",
        "start",
        "stop",
        "restart",
        "remove",
    ]
    req, resp = get_procedure_schemas("container_control", "v1", "list")
    assert resp.full_name == (
        "keelson.interfaces.container_control.ListContainersResponse"
    )
    # The process that serves this and the stations that call it live in
    # different repos; the procedure names are what both put in the RPC key.
    assert get_procedures("navigation_control", "v1") == [
        "load_route",
        "set_guidance_order",
        "set_voyage_status",
        "get_navigation_state",
    ]
    # The reply is the broadcast payload itself, not a copy of it.
    _, resp = get_procedure_schemas("navigation_control", "v1", "get_navigation_state")
    assert resp.full_name == "keelson.NavigationState"
    # One type gives the order and reports the order in force.
    req, _ = get_procedure_schemas("navigation_control", "v1", "set_guidance_order")
    order = req.fields_by_name["order"].message_type
    assert order.full_name == "keelson.GuidanceOrder"
    # Every frame is one member of the same oneof, so an order names exactly one of them. go_to
    # carries the canonical Coordinate, not a pair of loose doubles.
    frames = [f.name for f in order.oneofs_by_name["order"].fields]
    assert frames == ["track", "course_over_ground_deg", "heading_deg", "hold", "go_to"]
    go_to = order.fields_by_name["go_to"].message_type
    assert go_to.fields_by_name["position"].message_type.full_name == "keelson.Coordinate"
    assert go_to.fields_by_name["arrival_radius_m"].has_presence
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
def test_guidance_order_go_to_roundtrips():
    ReqCls, _ = get_procedure_message_classes("navigation_control", "v1", "set_guidance_order")
    req = ReqCls()
    req.order.go_to.position.latitude_deg = 57.7
    req.order.go_to.position.longitude_deg = 11.9
    req.order.speed_knots = 6.0
    decoded = ReqCls.FromString(req.SerializeToString())
    assert decoded.order.WhichOneof("order") == "go_to"
    assert (decoded.order.go_to.position.latitude_deg, decoded.order.go_to.position.longitude_deg) == (57.7, 11.9)
    # Absent is the responder's own threshold, not a radius of zero.
    assert not decoded.order.go_to.HasField("arrival_radius_m")
    assert decoded.order.speed_knots == 6.0


@pytest.mark.unit
def test_descriptor_set_bytes_cover_domain_imports():
    from google.protobuf.descriptor_pb2 import FileDescriptorSet

    fds = FileDescriptorSet.FromString(get_interfaces_file_descriptor_set())
    names = {f.name for f in fds.file}
    assert "VehicleMission.proto" in names
    assert "Mission.proto" in names  # --include_imports pulls the domain pool in
    assert "Coordinate.proto" in names
    # ContainerInfo lives in the payload pool and is imported by the interface.
    assert "ContainerHost.proto" in names


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
