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


def test_platform_geometry_help(run_connector):
    """Test that platform-geometry --help returns successfully."""
    result = run_connector("platform", "platform-geometry", ["--help"])

    assert result.returncode == 0
    assert "platform" in result.stdout.lower()
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout
    assert "--source-id" in result.stdout or "-s" in result.stdout
    assert "--config" in result.stdout


def test_platform_geometry_missing_required_args(run_connector):
    """Test that platform-geometry fails gracefully when required args are missing."""
    result = run_connector("platform", "platform-geometry", [])

    assert result.returncode != 0


def test_platform_geometry_missing_realm_arg(run_connector):
    """Test that platform-geometry fails when --realm is missing."""
    result = run_connector(
        "platform",
        "platform-geometry",
        [
            "--entity-id",
            "test-entity",
            "--source-id",
            "test-source",
            "--config",
            "/tmp/config.json",
        ],
    )

    assert result.returncode != 0


def test_platform_geometry_missing_entity_id_arg(run_connector):
    """Test that platform-geometry fails when --entity-id is missing."""
    result = run_connector(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--source-id",
            "test-source",
            "--config",
            "/tmp/config.json",
        ],
    )

    assert result.returncode != 0


def test_platform_geometry_missing_source_id_arg(run_connector):
    """Test that platform-geometry fails when --source-id is missing."""
    result = run_connector(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-entity",
            "--config",
            "/tmp/config.json",
        ],
    )

    assert result.returncode != 0


def test_platform_geometry_missing_config_arg(run_connector):
    """Test that platform-geometry fails when --config is missing."""
    result = run_connector(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-entity",
            "--source-id",
            "test-source",
        ],
    )

    assert result.returncode != 0


def test_platform_geometry_config_file_not_found(run_connector, temp_dir: Path):
    """Test that platform-geometry fails gracefully when config file doesn't exist."""
    result = run_connector(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-entity",
            "--source-id",
            "test-source",
            "--config",
            str(temp_dir / "nonexistent.json"),
        ],
    )

    assert result.returncode != 0


def test_platform_geometry_invalid_json_config(run_connector, temp_dir: Path):
    """Test that platform-geometry fails gracefully with invalid JSON config."""
    # Create an invalid JSON file
    config_path = temp_dir / "invalid.json"
    config_path.write_text("not valid json {{{")

    result = run_connector(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-entity",
            "--source-id",
            "test-source",
            "--config",
            str(config_path),
        ],
    )

    assert result.returncode != 0


def test_platform_geometry_shows_optional_args(run_connector):
    """Test that platform-geometry help documents optional args."""
    result = run_connector("platform", "platform-geometry", ["--help"])

    assert result.returncode == 0
    # Check that optional args are documented
    assert "--interval" in result.stdout


def test_platform_geometry_runs_with_valid_config(
    connector_process_factory, temp_dir: Path
):
    """Test that platform-geometry runs successfully with a valid config."""
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

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    # Start platform-geometry
    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "geometry",
            "--config",
            str(config_path),
            "--interval",
            "1",
        ],
    )
    platform.start()

    # Give it time to start
    time.sleep(2)

    # Verify it's still running (hasn't crashed)
    assert platform.is_running(), "platform-geometry should be running"

    # Stop it
    platform.stop()


def test_platform_geometry_data_recorded(connector_process_factory, temp_dir: Path):
    """Test that platform-geometry data can be recorded by mcap-record."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # Create a valid platform config
    config = {
        "vessel_name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    # Start recorder first
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
        ],
    )
    recorder.start()

    time.sleep(1)

    # Start platform geometry publisher
    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "geometry",
            "--config",
            str(config_path),
            "--interval",
            "1",
        ],
    )
    platform.start()

    # Let them run
    time.sleep(3)

    # Stop both
    platform.stop()
    recorder.stop()

    # Verify MCAP file contains platform data
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert file_size > 0, "MCAP file should contain platform data"
