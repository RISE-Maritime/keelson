"""
End-to-end tests for the Foxglove connector.

Tests the foxglove-liveview WebSocket server functionality.
"""

import importlib.util
import pathlib
import socket
import sys
import time
from importlib.machinery import SourceFileLoader
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import zenoh

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.ReplayControl_pb2 import ReplaySuccessResponse, SetSpeedRequest
from keelson.scaffolding import RpcOp, serve_rpc

# Import the connector script dynamically so RpcServiceBridge can be
# exercised in-process against a real zenoh session.
BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))
_loader = SourceFileLoader(
    "keelson2foxglove_e2e", str(BIN_ROOT / "keelson2foxglove.py")
)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
keelson2foxglove = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(keelson2foxglove)


@pytest.mark.e2e
def test_foxglove_liveview_starts_server(connector_process_factory):
    """Test that foxglove-liveview starts the WebSocket server successfully."""
    server = connector_process_factory(
        "foxglove",
        "foxglove-liveview",
        ["--key", "test/**", "--ws-host", "127.0.0.1", "--ws-port", "18765"],
    )
    server.start()
    time.sleep(2)

    assert server.is_running(), "foxglove-liveview should be running"
    server.stop()


@pytest.mark.e2e
def test_foxglove_liveview_accepts_websocket(connector_process_factory):
    """Test that foxglove-liveview accepts WebSocket connections."""
    port = 18766

    server = connector_process_factory(
        "foxglove",
        "foxglove-liveview",
        ["--key", "test/**", "--ws-host", "127.0.0.1", "--ws-port", str(port)],
    )
    server.start()
    time.sleep(2)

    connected = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", port))
        connected = result == 0
        sock.close()
    except Exception:
        pass

    server.stop()
    assert connected, f"Should be able to connect to WebSocket port {port}"


@pytest.mark.e2e
def test_foxglove_liveview_with_zenoh_data(connector_process_factory, zenoh_endpoints):
    """Test that foxglove-liveview can receive Zenoh data."""
    port = 18767

    radar = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "radar1",
            "--spokes_per_sweep",
            "5",
            "--seconds_per_sweep",
            "0.5",
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    radar.start()
    time.sleep(1)

    server = connector_process_factory(
        "foxglove",
        "foxglove-liveview",
        [
            "--key",
            "test-realm/@v0/**",
            "--ws-host",
            "127.0.0.1",
            "--ws-port",
            str(port),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    server.start()
    time.sleep(2)

    assert server.is_running(), "foxglove-liveview should be running"

    connected = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", port))
        connected = result == 0
        sock.close()
    except Exception:
        pass

    server.stop()
    radar.stop()

    assert connected, "WebSocket should be accessible while receiving Zenoh data"


@pytest.mark.e2e
def test_foxglove_liveview_advertises_rpc_services(
    connector_process_factory, zenoh_endpoints
):
    """foxglove-liveview started with --expose-rpc-services should discover
    a live replay_control/v1 responder via interface-level liveliness and
    log that it advertised Foxglove services for it."""
    port = 18768
    realm = "test-realm"

    def _set_speed(op: RpcOp):
        op.reply_ok(ReplaySuccessResponse())

    def _seek(op: RpcOp):
        op.reply_err("no file loaded", ErrorResponse.Code.INVALID_STATE)

    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    conf.insert_json5("listen/endpoints", f'["{zenoh_endpoints["listen"]}"]')

    with zenoh.open(conf) as session:
        serve_rpc(
            session,
            base_path=realm,
            entity_id="test-vessel",
            responder_id="mcap/0",
            interface="replay_control",
            version="v1",
            handlers={"set_speed": _set_speed, "seek": _seek},
        )
        # Give the queryable + liveliness token declarations a moment to
        # settle before the bridge process starts monitoring.
        time.sleep(0.5)

        server = connector_process_factory(
            "foxglove",
            "foxglove-liveview",
            [
                "--key",
                "test/**",
                "--ws-host",
                "127.0.0.1",
                "--ws-port",
                str(port),
                "--mode",
                "peer",
                "--connect",
                zenoh_endpoints["connect"],
                "--expose-rpc-services",
                realm,
            ],
        )
        server.start()
        # Generous sleep for: server startup, liveliness history query,
        # on_join dispatch, and service advertisement. CI is slow.
        time.sleep(6.0)

        assert server.is_running(), "foxglove-liveview should still be running"
        server.stop()

        _stdout, stderr = server.logs()
        assert "Advertised" in stderr and "replay_control" in stderr, (
            "Expected an 'Advertised N Foxglove services for ...' log line "
            f"mentioning replay_control. stderr: {stderr[-4000:]}"
        )


@pytest.mark.e2e
def test_rpc_service_bridge_call_path_round_trip():
    """The Foxglove-handler → zenoh RPC call path, over a real zenoh
    session: on_join builds handlers whose invocation reaches a real
    serve_rpc responder, and the raw reply bytes come back unchanged."""
    realm = "test-bridge-call-realm"
    entity = "test-vessel"
    responder = "mcap/0"

    seen = {}

    def _set_speed(op: RpcOp):
        request = SetSpeedRequest()
        request.ParseFromString(op.request_bytes)
        seen["speed"] = request.speed
        op.reply_ok(ReplaySuccessResponse())

    def _seek(op: RpcOp):
        op.reply_err("no file loaded", ErrorResponse.Code.INVALID_STATE)

    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')

    with zenoh.open(conf) as session:
        serve_rpc(
            session,
            base_path=realm,
            entity_id=entity,
            responder_id=responder,
            interface="replay_control",
            version="v1",
            handlers={"set_speed": _set_speed, "seek": _seek},
        )
        # Give the queryable declarations a moment to settle.
        time.sleep(0.5)

        mock_server = Mock()
        bridge = keelson2foxglove.RpcServiceBridge(
            session, mock_server, call_timeout=5.0
        )
        token_key = keelson.construct_rpc_interface_liveliness_key(
            realm, entity, "replay_control", "v1", responder
        )
        bridge.on_join(token_key)

        mock_server.add_services.assert_called_once()
        (services,), _ = mock_server.add_services.call_args
        services_by_name = {service.name: service for service in services}

        # OK path: invoke the bound set_speed handler with a fake
        # ServiceRequest; the returned bytes must parse as the responder's
        # ReplaySuccessResponse.
        set_speed = services_by_name[
            f"{realm}/{entity}/replay_control/v1/set_speed/{responder}"
        ]
        fake_request = SimpleNamespace(
            payload=SetSpeedRequest(speed=2.5).SerializeToString()
        )
        response_bytes = set_speed.handler(fake_request)
        response = ReplaySuccessResponse()
        response.ParseFromString(response_bytes)  # must not raise
        assert seen["speed"] == pytest.approx(2.5)

        # Error path: the responder's reply_err surfaces as a raised
        # exception whose message carries the typed code.
        seek = services_by_name[f"{realm}/{entity}/replay_control/v1/seek/{responder}"]
        with pytest.raises(Exception, match="INVALID_STATE: no file loaded"):
            seek.handler(SimpleNamespace(payload=b""))

        bridge.close()
