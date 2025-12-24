"""
End-to-end tests for the Platform connector CLI.

Tests the following command:
- platform-geometry: Publishes static platform geometry information to Zenoh
"""

import json
import time
from pathlib import Path


# =============================================================================
# platform-geometry CLI tests
# =============================================================================


def test_platform_geometry_help(run_in_container):
    """Test that platform-geometry --help returns successfully."""
    result = run_in_container("platform-geometry --help")

    assert result.returncode == 0
    assert "platform" in result.stdout.lower()
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout
    assert "--source-id" in result.stdout or "-s" in result.stdout
    assert "--config" in result.stdout


def test_platform_geometry_missing_required_args(run_in_container):
    """Test that platform-geometry fails gracefully when required args are missing."""
    result = run_in_container("platform-geometry")

    assert result.returncode != 0


def test_platform_geometry_missing_realm_arg(run_in_container):
    """Test that platform-geometry fails when --realm is missing."""
    result = run_in_container(
        "platform-geometry --entity-id test-entity "
        "--source-id test-source --config /tmp/config.json"
    )

    assert result.returncode != 0


def test_platform_geometry_missing_entity_id_arg(run_in_container):
    """Test that platform-geometry fails when --entity-id is missing."""
    result = run_in_container(
        "platform-geometry --realm test-realm "
        "--source-id test-source --config /tmp/config.json"
    )

    assert result.returncode != 0


def test_platform_geometry_missing_source_id_arg(run_in_container):
    """Test that platform-geometry fails when --source-id is missing."""
    result = run_in_container(
        "platform-geometry --realm test-realm "
        "--entity-id test-entity --config /tmp/config.json"
    )

    assert result.returncode != 0


def test_platform_geometry_missing_config_arg(run_in_container):
    """Test that platform-geometry fails when --config is missing."""
    result = run_in_container(
        "platform-geometry --realm test-realm "
        "--entity-id test-entity --source-id test-source"
    )

    assert result.returncode != 0


def test_platform_geometry_config_file_not_found(run_in_container, temp_dir: Path):
    """Test that platform-geometry fails gracefully when config file doesn't exist."""
    result = run_in_container(
        "platform-geometry --realm test-realm --entity-id test-entity "
        "--source-id test-source --config /data/nonexistent.json",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0


def test_platform_geometry_invalid_json_config(run_in_container, temp_dir: Path):
    """Test that platform-geometry fails gracefully with invalid JSON config."""
    # Create an invalid JSON file
    config_path = temp_dir / "invalid.json"
    config_path.write_text("not valid json {{{")
    config_path.chmod(0o644)

    result = run_in_container(
        "platform-geometry --realm test-realm --entity-id test-entity "
        "--source-id test-source --config /data/invalid.json",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0


def test_platform_geometry_shows_optional_args(run_in_container):
    """Test that platform-geometry help documents optional args."""
    result = run_in_container("platform-geometry --help")

    assert result.returncode == 0
    # Check that optional args are documented
    assert "--interval" in result.stdout


def test_platform_geometry_runs_with_valid_config(
    container_factory, docker_network, temp_dir: Path
):
    """Test that platform-geometry runs successfully with a valid config."""
    config_dir = temp_dir / "config"
    config_dir.mkdir()
    config_dir.chmod(0o777)

    # Create a valid platform config
    config = {
        "vessel_name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
        "frame_transforms": [
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "radar",
                "translation": {"x": 5.0, "y": 0.0, "z": 10.0},
                "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            }
        ],
    }

    config_path = config_dir / "platform.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o644)

    # Start platform-geometry with a short interval and timeout
    platform = container_factory(
        command=(
            "timeout --signal=INT 3 "
            "platform-geometry --realm test-realm --entity-id test-vessel "
            "--source-id geometry --config /data/platform.json --interval 1"
        ),
        network=docker_network.name,
        volumes={str(config_dir): "/data"},
    )
    platform.start()

    # Give it time to start
    time.sleep(1)

    # Verify it's still running (hasn't crashed)
    assert platform.is_running(), "platform-geometry should be running"

    # Wait for it to finish
    platform.wait(timeout=10)

    # Check logs don't contain errors
    stdout, stderr = platform.logs()
    combined = stdout + stderr
    assert "error" not in combined.lower() or "Putting to" in combined


def test_platform_geometry_data_recorded(
    container_factory, docker_network, temp_dir: Path
):
    """Test that platform-geometry data can be recorded by mcap-record."""
    data_dir = temp_dir / "data"
    data_dir.mkdir()
    data_dir.chmod(0o777)

    # Create a valid platform config
    config = {
        "vessel_name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
    }

    config_path = data_dir / "platform.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o644)

    # Start recorder first
    recorder = container_factory(
        command=(
            "timeout --signal=INT 5 "
            "mcap-record --key 'test-realm/**' --output-folder /data "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(data_dir): "/data"},
    )
    recorder.start()

    time.sleep(1)

    # Start platform geometry publisher
    platform = container_factory(
        command=(
            "timeout --signal=INT 3 "
            "platform-geometry --realm test-realm --entity-id test-vessel "
            "--source-id geometry --config /data/platform.json --interval 1"
        ),
        network=docker_network.name,
        volumes={str(data_dir): "/data"},
    )
    platform.start()

    # Wait for both to finish
    platform.wait(timeout=10)
    recorder.wait(timeout=10)

    # Verify MCAP file contains platform data
    mcap_files = list(data_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert file_size > 0, "MCAP file should contain platform data"
