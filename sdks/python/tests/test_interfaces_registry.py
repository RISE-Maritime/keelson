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


# ---------------------------------------------------------------------------
# Caller on the vehicle interfaces (crowsnest-dev docs/COMMAND-ARCHITECTURE.md §4)
# ---------------------------------------------------------------------------

CALLER = "keelson.interfaces.common.Caller"

# (interface, procedure): every mutating request names who calls and under
# which fence. A server that ignores the field is unchanged.
_CARRIES_A_CALLER = [
    ("navigation_control", "load_route"),
    ("navigation_control", "set_guidance_order"),
    ("navigation_control", "set_voyage_status"),
    ("vehicle_control", "set_control_mapping"),
    ("vehicle_navigation", "set_navigation_target"),
    ("vehicle_navigation", "set_cruise_speed"),
    ("vehicle_navigation", "set_steering_order"),
    ("vehicle_mission", "clear_mission"),
    ("vehicle_mission", "set_current_waypoint"),
    ("vehicle_lifecycle", "arm"),
    ("vehicle_lifecycle", "set_mode"),
]


@pytest.mark.unit
@pytest.mark.parametrize("interface, procedure", _CARRIES_A_CALLER)
def test_mutating_requests_carry_the_common_caller(interface, procedure):
    req, _ = get_procedure_schemas(interface, "v1", procedure)
    caller = req.fields_by_name["caller"]
    assert caller.message_type.full_name == CALLER
    assert caller.has_presence, "absence must be observable: a client built before"


@pytest.mark.unit
def test_emergency_stop_is_caller_free_on_purpose():
    """A stop must never depend on holding a lease. The ACL still applies."""
    req, _ = get_procedure_schemas("vehicle_lifecycle", "v1", "emergency_stop")
    assert "caller" not in req.fields_by_name
    assert not any(
        f.message_type is not None and f.message_type.full_name == CALLER
        for f in req.fields
    )


@pytest.mark.unit
def test_upload_mission_still_takes_the_domain_type():
    """Giving it a Caller means a request wrapper, which retypes the procedure
    and is a v2. Pinned so that nobody adds `caller` to keelson.Mission."""
    req, _ = get_procedure_schemas("vehicle_mission", "v1", "upload_mission")
    assert req.full_name == "keelson.Mission"
    assert "caller" not in req.fields_by_name


@pytest.mark.unit
def test_the_caller_carries_the_domains_fence_and_keeps_its_old_numbers():
    from keelson.interfaces.VehicleCommon_pb2 import Caller

    fields = Caller.DESCRIPTOR.fields_by_name
    # Field numbers 1 and 2 are the ones NavigationControl.proto had, so a
    # request encoded against the old Caller decodes against this one.
    assert fields["controller_id"].number == 1
    assert fields["authority_token"].number == 2
    assert fields["authority_token"].has_presence
    assert fields["fence"].message_type.full_name == "keelson.Fence"
    assert fields["fence"].has_presence
    assert fields["assignment_revision"].has_presence
    # The interfaces descriptor set carries the payload it imports.
    from google.protobuf.descriptor_pb2 import FileDescriptorSet

    fds = FileDescriptorSet.FromString(get_interfaces_file_descriptor_set())
    assert "Fence.proto" in {f.name for f in fds.file}


@pytest.mark.unit
def test_an_old_navigation_control_request_decodes_with_the_moved_caller():
    from keelson.interfaces.NavigationControl_pb2 import LoadRouteRequest
    from keelson.interfaces.VehicleCommon_pb2 import Caller

    # Wire bytes of the pre-move shape: caller {controller_id = 1, token = 2}.
    old_caller = Caller(controller_id="ted@ROC-1", authority_token="ab" * 16)
    msg = LoadRouteRequest(caller=old_caller)
    decoded = LoadRouteRequest.FromString(msg.SerializeToString())
    assert decoded.caller.controller_id == "ted@ROC-1"
    assert decoded.caller.authority_token == "ab" * 16
    assert not decoded.caller.HasField("fence")
