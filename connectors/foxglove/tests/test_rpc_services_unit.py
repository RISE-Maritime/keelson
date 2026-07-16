#!/usr/bin/env python3

"""Unit tests for RpcServiceBridge in keelson2foxglove.py.

Exercises the liveliness-driven discovery of keelson RPC endpoints and
their advertisement as Foxglove services, against Mock server/session
objects (no real Zenoh or Foxglove server involved).
"""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader
from unittest.mock import Mock

import pytest

from keelson.interfaces import get_procedure_schemas, get_procedures
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.ReplayControl_pb2 import ReplaySuccessResponse

# Path to the bin root
BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))

# Import the script dynamically
_script_path = BIN_ROOT / "keelson2foxglove.py"
_loader = SourceFileLoader("keelson2foxglove", str(_script_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
keelson2foxglove = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(keelson2foxglove)

RpcServiceBridge = keelson2foxglove.RpcServiceBridge

REPLAY_CONTROL_TOKEN_KEY = "realm/@v0/boat/@rpc/replay_control/v1/*/mcap/0"
CONFIGURABLE_TOKEN_KEY = "realm/@v0/boat/@rpc/configurable/v1/*/cfg/0"
UNKNOWN_INTERFACE_TOKEN_KEY = "realm/@v0/boat/@rpc/custom_iface/v1/*/x/0"

REPLAY_CONTROL_PROCEDURES = get_procedures("replay_control", "v1")


def _make_ok_reply(payload_bytes: bytes):
    reply = Mock()
    reply.ok.payload.to_bytes = Mock(return_value=payload_bytes)
    return reply


def _make_err_reply(code: int, description: str):
    reply = Mock()
    reply.ok = None
    reply.err.payload.to_bytes = Mock(
        return_value=ErrorResponse(
            code=code, error_description=description
        ).SerializeToString()
    )
    return reply


@pytest.fixture
def mock_server():
    server = Mock()
    server.add_services = Mock()
    server.remove_services = Mock()
    return server


@pytest.fixture
def mock_session():
    return Mock()


@pytest.fixture
def bridge(mock_session, mock_server):
    return RpcServiceBridge(mock_session, mock_server)


class TestOnJoin:
    def test_adds_one_service_per_procedure(self, bridge, mock_server):
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)

        mock_server.add_services.assert_called_once()
        (services_arg,), _ = mock_server.add_services.call_args
        assert len(services_arg) == len(REPLAY_CONTROL_PROCEDURES)

        expected_names = {
            f"realm/boat/replay_control/v1/{procedure}/mcap/0"
            for procedure in REPLAY_CONTROL_PROCEDURES
        }
        actual_names = {service.name for service in services_arg}
        assert actual_names == expected_names

    def test_service_schemas_match_procedure_schemas(self, bridge, mock_server):
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)

        (services_arg,), _ = mock_server.add_services.call_args
        services_by_name = {service.name: service for service in services_arg}

        for procedure in REPLAY_CONTROL_PROCEDURES:
            name = f"realm/boat/replay_control/v1/{procedure}/mcap/0"
            service = services_by_name[name]
            request_desc, response_desc = get_procedure_schemas(
                "replay_control", "v1", procedure
            )
            assert service.schema.name == name
            assert service.schema.request.schema.name == request_desc.full_name
            assert service.schema.response.schema.name == response_desc.full_name
            assert service.schema.request.encoding == "protobuf"
            assert service.schema.response.encoding == "protobuf"

    def test_json_placeholder_sides_advertised_as_json(self, bridge, mock_server):
        """configurable/v1's JSON{} placeholder sides (set_config request,
        get_config response) must be advertised as json/jsonschema; the
        genuinely-protobuf sides stay protobuf."""
        bridge.on_join(CONFIGURABLE_TOKEN_KEY)

        (services_arg,), _ = mock_server.add_services.call_args
        services_by_name = {service.name: service for service in services_arg}

        get_config = services_by_name["realm/boat/configurable/v1/get_config/cfg/0"]
        set_config = services_by_name["realm/boat/configurable/v1/set_config/cfg/0"]

        # JSON placeholder sides
        assert get_config.schema.response.encoding == "json"
        assert get_config.schema.response.schema.encoding == "jsonschema"
        assert set_config.schema.request.encoding == "json"
        assert set_config.schema.request.schema.encoding == "jsonschema"

        # Protobuf sides remain protobuf
        assert get_config.schema.request.encoding == "protobuf"
        assert set_config.schema.response.encoding == "protobuf"

    def test_two_base_paths_produce_distinct_names(self, bridge, mock_server):
        """The same entity/interface/source under two base paths must not
        collide: distinct service names, independently removable."""
        token_a = "realm-a/@v0/boat/@rpc/replay_control/v1/*/mcap/0"
        token_b = "realm-b/@v0/boat/@rpc/replay_control/v1/*/mcap/0"

        bridge.on_join(token_a)
        bridge.on_join(token_b)

        assert mock_server.add_services.call_count == 2
        (services_a,), _ = mock_server.add_services.call_args_list[0]
        (services_b,), _ = mock_server.add_services.call_args_list[1]
        names_a = {service.name for service in services_a}
        names_b = {service.name for service in services_b}
        assert names_a.isdisjoint(names_b)
        assert all(name.startswith("realm-a/") for name in names_a)
        assert all(name.startswith("realm-b/") for name in names_b)

        # Leaving one realm removes only that realm's services.
        bridge.on_leave(token_a)
        mock_server.remove_services.assert_called_once()
        (removed,), _ = mock_server.remove_services.call_args
        assert set(removed) == names_a
        assert token_b in bridge._services

    def test_duplicate_on_join_is_a_no_op(self, bridge, mock_server):
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)

        mock_server.add_services.assert_called_once()

    def test_unknown_interface_warns_and_adds_nothing(
        self, bridge, mock_server, caplog
    ):
        with caplog.at_level("WARNING"):
            bridge.on_join(UNKNOWN_INTERFACE_TOKEN_KEY)

        mock_server.add_services.assert_not_called()
        assert len(caplog.records) == 1
        assert "not in this SDK's interfaces.yaml" in caplog.records[0].message

    def test_unknown_interface_does_not_re_warn_on_rejoin(
        self, bridge, mock_server, caplog
    ):
        with caplog.at_level("WARNING"):
            bridge.on_join(UNKNOWN_INTERFACE_TOKEN_KEY)
            bridge.on_join(UNKNOWN_INTERFACE_TOKEN_KEY)

        mock_server.add_services.assert_not_called()
        assert len(caplog.records) == 1

    def test_unparseable_key_is_ignored(self, bridge, mock_server, caplog):
        with caplog.at_level("DEBUG"):
            bridge.on_join("not-a-valid-liveliness-key")

        mock_server.add_services.assert_not_called()
        assert "not-a-valid-liveliness-key" not in bridge._services


class TestOnLeave:
    def test_removes_exactly_the_added_names(self, bridge, mock_server):
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)
        (services_arg,), _ = mock_server.add_services.call_args
        added_names = {service.name for service in services_arg}

        bridge.on_leave(REPLAY_CONTROL_TOKEN_KEY)

        mock_server.remove_services.assert_called_once()
        (removed_names,), _ = mock_server.remove_services.call_args
        assert set(removed_names) == added_names
        assert REPLAY_CONTROL_TOKEN_KEY not in bridge._services

    def test_leave_without_join_is_a_no_op(self, bridge, mock_server):
        bridge.on_leave("some/unknown/token/key")
        mock_server.remove_services.assert_not_called()

    def test_leave_for_unknown_interface_entry_is_a_no_op(self, bridge, mock_server):
        bridge.on_join(UNKNOWN_INTERFACE_TOKEN_KEY)
        bridge.on_leave(UNKNOWN_INTERFACE_TOKEN_KEY)
        mock_server.remove_services.assert_not_called()


class TestCall:
    def test_ok_reply_bytes_are_passed_through_unchanged(self, bridge, mock_session):
        response_bytes = ReplaySuccessResponse().SerializeToString()
        mock_session.get = Mock(return_value=iter([_make_ok_reply(response_bytes)]))

        request = Mock()
        request.payload = b"\x01\x02\x03"

        result = bridge._call(
            "realm", "boat", "replay_control", "v1", "set_speed", "mcap/0", request
        )

        assert result == response_bytes
        mock_session.get.assert_called_once_with(
            "realm/@v0/boat/@rpc/replay_control/v1/set_speed/mcap/0",
            payload=b"\x01\x02\x03",
            timeout=10.0,
        )

    def test_json_payloads_are_passed_through_raw(self, bridge, mock_session):
        """A raw-JSON procedure (configurable/v1) must round-trip its bytes
        with no protobuf decode attempt — this is what invoke_procedure
        could not do."""
        raw_json = b'{"speed_limit_kn": 12}'
        mock_session.get = Mock(return_value=iter([_make_ok_reply(raw_json)]))

        request = Mock()
        request.payload = b"{}"

        result = bridge._call(
            "realm", "boat", "configurable", "v1", "get_config", "cfg/0", request
        )

        assert result == raw_json

    def test_err_reply_raises_with_code_and_description(self, bridge, mock_session):
        mock_session.get = Mock(
            return_value=iter(
                [_make_err_reply(ErrorResponse.Code.INVALID_STATE, "no file loaded")]
            )
        )

        request = Mock()
        request.payload = b""

        with pytest.raises(RuntimeError, match="INVALID_STATE: no file loaded"):
            bridge._call(
                "realm", "boat", "replay_control", "v1", "seek", "mcap/0", request
            )

    def test_no_reply_raises_timeout_naming_the_endpoint(self, bridge, mock_session):
        mock_session.get = Mock(return_value=iter([]))

        request = Mock()
        request.payload = b""

        with pytest.raises(TimeoutError, match="replay_control/v1/play/mcap/0"):
            bridge._call(
                "realm", "boat", "replay_control", "v1", "play", "mcap/0", request
            )

    def test_call_timeout_is_plumbed_through(self, mock_session, mock_server):
        bridge = RpcServiceBridge(mock_session, mock_server, call_timeout=2.5)
        mock_session.get = Mock(return_value=iter([_make_ok_reply(b"")]))

        request = Mock()
        request.payload = b""

        bridge._call("realm", "boat", "replay_control", "v1", "play", "mcap/0", request)
        _, kwargs = mock_session.get.call_args
        assert kwargs["timeout"] == 2.5


class TestClose:
    def test_removes_everything(self, bridge, mock_server):
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)
        mock_server.add_services.reset_mock()

        bridge.close()

        mock_server.remove_services.assert_called_once()
        (removed_names,), _ = mock_server.remove_services.call_args
        assert len(removed_names) == len(REPLAY_CONTROL_PROCEDURES)
        assert bridge._services == {}

    def test_close_with_nothing_advertised_is_a_no_op(self, bridge, mock_server):
        bridge.close()
        mock_server.remove_services.assert_not_called()
