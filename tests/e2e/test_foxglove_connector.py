"""
End-to-end tests for the Foxglove connector CLI.

Tests the following command:
- foxglove-liveview: WebSocket server for real-time Foxglove visualization
"""

import socket
import time


# =============================================================================
# foxglove-liveview CLI tests
# =============================================================================


def test_foxglove_liveview_help(run_connector):
    """Test that foxglove-liveview --help returns successfully."""
    result = run_connector("foxglove", "foxglove-liveview", ["--help"])

    assert result.returncode == 0
    assert "foxglove-liveview" in result.stdout
    assert "--key" in result.stdout or "-k" in result.stdout
    assert "--ws-host" in result.stdout
    assert "--ws-port" in result.stdout


def test_foxglove_liveview_starts_server(connector_process_factory):
    """Test that foxglove-liveview starts the WebSocket server successfully."""
    # Start foxglove-liveview
    server = connector_process_factory(
        "foxglove",
        "foxglove-liveview",
        ["--key", "test/**", "--ws-host", "127.0.0.1", "--ws-port", "18765"],
    )
    server.start()

    # Give the server time to start
    time.sleep(2)

    # Check that the server is still running (hasn't crashed)
    assert server.is_running(), "foxglove-liveview should be running"

    # Stop the server
    server.stop()


def test_foxglove_liveview_accepts_websocket(connector_process_factory):
    """Test that foxglove-liveview accepts WebSocket connections."""
    port = 18766

    # Start foxglove-liveview
    server = connector_process_factory(
        "foxglove",
        "foxglove-liveview",
        ["--key", "test/**", "--ws-host", "127.0.0.1", "--ws-port", str(port)],
    )
    server.start()

    # Give the server time to start
    time.sleep(2)

    # Try to connect to the WebSocket port
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


def test_foxglove_liveview_with_zenoh_data(connector_process_factory, zenoh_endpoints):
    """Test that foxglove-liveview can receive Zenoh data."""
    port = 18767

    # Start a radar publisher first (it listens)
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

    # Start foxglove-liveview with explicit Zenoh connection
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

    # Verify server is running
    assert server.is_running(), "foxglove-liveview should be running"

    # Verify WebSocket is accessible
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
