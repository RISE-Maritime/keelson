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
    assert "required" in result.stderr.lower() or "error" in result.stderr.lower()


def test_foxglove_liveview_missing_key_arg(run_in_container):
    """Test that foxglove-liveview fails when --key is missing."""
    result = run_in_container("foxglove-liveview --ws-port 8765")

    assert result.returncode != 0
    assert "key" in result.stderr.lower() or "required" in result.stderr.lower()


def test_foxglove_liveview_shows_default_values(run_in_container):
    """Test that foxglove-liveview help shows default values for optional args."""
    result = run_in_container("foxglove-liveview --help")

    assert result.returncode == 0
    # Check that default values are documented
    assert "127.0.0.1" in result.stdout or "localhost" in result.stdout.lower()
    assert "8765" in result.stdout
