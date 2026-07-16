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

from keelson.interfaces import RpcError, get_procedure_schemas, get_procedures
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
UNKNOWN_INTERFACE_TOKEN_KEY = "realm/@v0/boat/@rpc/custom_iface/v1/*/x/0"

REPLAY_CONTROL_PROCEDURES = get_procedures("replay_control", "v1")


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
            f"boat/replay_control/v1/{procedure}/mcap/0"
            for procedure in REPLAY_CONTROL_PROCEDURES
        }
        actual_names = {service.name for service in services_arg}
        assert actual_names == expected_names

    def test_service_schemas_match_procedure_schemas(self, bridge, mock_server):
        bridge.on_join(REPLAY_CONTROL_TOKEN_KEY)

        (services_arg,), _ = mock_server.add_services.call_args
        services_by_name = {service.name: service for service in services_arg}

        for procedure in REPLAY_CONTROL_PROCEDURES:
            name = f"boat/replay_control/v1/{procedure}/mcap/0"
            service = services_by_name[name]
            request_desc, response_desc = get_procedure_schemas(
                "replay_control", "v1", procedure
            )
            assert service.schema.name == name
            assert service.schema.request.schema.name == request_desc.full_name
            assert service.schema.response.schema.name == response_desc.full_name
            assert service.schema.request.encoding == "protobuf"
            assert service.schema.response.encoding == "protobuf"

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
    def test_round_trip_returns_serialized_response(self, bridge, monkeypatch):
        expected = ReplaySuccessResponse()
        invoke_mock = Mock(return_value=expected)
        monkeypatch.setattr(
            keelson2foxglove.keelson.interfaces, "invoke_procedure", invoke_mock
        )

        request = Mock()
        request.payload = b"\x01\x02\x03"

        result = bridge._call(
            "realm", "boat", "replay_control", "v1", "set_speed", "mcap/0", request
        )

        assert result == expected.SerializeToString()
        invoke_mock.assert_called_once_with(
            bridge._session,
            "realm",
            "boat",
            "replay_control",
            "v1",
            "set_speed",
            "mcap/0",
            request=b"\x01\x02\x03",
            timeout=10.0,
        )

    def test_propagates_rpc_error(self, bridge, monkeypatch):
        invoke_mock = Mock(side_effect=RpcError(1, "INVALID_STATE", "boom"))
        monkeypatch.setattr(
            keelson2foxglove.keelson.interfaces, "invoke_procedure", invoke_mock
        )

        request = Mock()
        request.payload = b""

        with pytest.raises(RpcError):
            bridge._call(
                "realm", "boat", "replay_control", "v1", "seek", "mcap/0", request
            )


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
