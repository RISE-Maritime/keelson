"""
End-to-end tests for the Platform connector CLI.

Tests the following command:
- platform-geometry: Publishes static platform geometry information to Zenoh
"""

import json
import time
from pathlib import Path

from mcap.reader import make_reader


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


def test_platform_geometry_data_recorded(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
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

    # Start recorder with explicit listen endpoint
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()

    time.sleep(1)

    # Start platform geometry publisher with explicit connect endpoint
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
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()

    # Let them run
    time.sleep(3)

    # Stop both
    platform.stop()
    recorder.stop()

    # Verify MCAP file contains platform data (> 500 bytes, not just empty header)
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert file_size > 500, "MCAP file should contain platform data"


def test_platform_geometry_publishes_correct_topic(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that platform-geometry publishes to the correct keelson topic format."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    config = {
        "vessel_name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    # Start recorder
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    # Start platform geometry
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
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()
    time.sleep(3)

    platform.stop()
    recorder.stop()

    # Read MCAP and verify topic format
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        topics = [ch.topic for ch in summary.channels.values()]

        # Should have at least one topic matching keelson format
        assert len(topics) > 0, "Should have recorded topics"

        # Topic should contain the entity-id
        entity_topics = [t for t in topics if "test-vessel" in t]
        assert len(entity_topics) > 0, "Topic should contain entity-id"


def test_platform_geometry_multiple_frames(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that platform-geometry publishes data for multiple frame transforms."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # Create config with multiple frame transforms
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
            },
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "camera_bow",
                "translation": {"x": 10.0, "y": 0.0, "z": 5.0},
                "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            },
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "camera_stern",
                "translation": {"x": -5.0, "y": 0.0, "z": 5.0},
                "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 3.14159},
            },
        ],
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    # Start recorder
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    # Start platform geometry
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
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()
    time.sleep(3)

    platform.stop()
    recorder.stop()

    # Verify data was recorded
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    file_size = mcap_files[0].stat().st_size
    assert file_size > 500, "MCAP file should contain platform geometry data"
