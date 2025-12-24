"""
End-to-end tests for the Foxglove connector CLI.

Tests the following command:
- foxglove-liveview: WebSocket server for real-time Foxglove visualization
"""

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


def test_foxglove_liveview_missing_required_args(run_connector):
    """Test that foxglove-liveview fails gracefully when required args are missing."""
    result = run_connector("foxglove", "foxglove-liveview", [])

    assert result.returncode != 0


def test_foxglove_liveview_missing_key_arg(run_connector):
    """Test that foxglove-liveview fails when --key is missing."""
    result = run_connector("foxglove", "foxglove-liveview", ["--ws-port", "8765"])

    assert result.returncode != 0


def test_foxglove_liveview_shows_optional_args(run_connector):
    """Test that foxglove-liveview help documents optional args."""
    result = run_connector("foxglove", "foxglove-liveview", ["--help"])

    assert result.returncode == 0
    # Check that optional args are documented
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
