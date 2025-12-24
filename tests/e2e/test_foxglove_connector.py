"""
End-to-end tests for the Foxglove connector CLI.

Tests the following command:
- foxglove-liveview: WebSocket server for real-time Foxglove visualization
"""


def test_foxglove_liveview_help(run_in_container):
    """Test that foxglove-liveview --help returns successfully."""
    result = run_in_container("foxglove-liveview --help")

    assert result.returncode == 0
    assert "foxglove-liveview" in result.stdout
    assert "--key" in result.stdout or "-k" in result.stdout
    assert "--ws-host" in result.stdout
    assert "--ws-port" in result.stdout


def test_foxglove_liveview_missing_required_args(run_in_container):
    """Test that foxglove-liveview fails gracefully when required args are missing."""
    result = run_in_container("foxglove-liveview")

    assert result.returncode != 0


def test_foxglove_liveview_missing_key_arg(run_in_container):
    """Test that foxglove-liveview fails when --key is missing."""
    result = run_in_container("foxglove-liveview --ws-port 8765")

    assert result.returncode != 0


def test_foxglove_liveview_shows_optional_args(run_in_container):
    """Test that foxglove-liveview help documents optional args."""
    result = run_in_container("foxglove-liveview --help")

    assert result.returncode == 0
    # Check that optional args are documented
    assert "--ws-host" in result.stdout
    assert "--ws-port" in result.stdout
