"""
End-to-end tests for the Foxglove connector.

Tests the foxglove-liveview WebSocket server functionality.
"""

import socket
import time

import pytest
import zenoh

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.ReplayControl_pb2 import ReplaySuccessResponse
from keelson.scaffolding import RpcOp, serve_rpc


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
