"""
End-to-end tests for the Mockups connector CLI.

Tests the following command:
- mockup_radar: Generates mock radar data for testing
"""

import time
from pathlib import Path


# =============================================================================
# mockup_radar CLI tests
# =============================================================================


def test_mockup_radar_help(run_connector):
    """Test that mockup_radar --help returns successfully."""
    result = run_connector("mockups", "mockup_radar", ["--help"])

    assert result.returncode == 0
    # The program name in help text might be 'fake_radar' based on the source
    assert "radar" in result.stdout.lower()
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout
    assert "--source-id" in result.stdout or "-s" in result.stdout


def test_mockup_radar_missing_required_args(run_connector):
    """Test that mockup_radar fails gracefully when required args are missing."""
    result = run_connector("mockups", "mockup_radar", [])

    assert result.returncode != 0


def test_mockup_radar_missing_realm_arg(run_connector):
    """Test that mockup_radar fails when --realm is missing."""
    result = run_connector(
        "mockups",
        "mockup_radar",
        ["--entity-id", "test-entity", "--source-id", "test-source"],
    )

    assert result.returncode != 0


def test_mockup_radar_missing_entity_id_arg(run_connector):
    """Test that mockup_radar fails when --entity-id is missing."""
    result = run_connector(
        "mockups",
        "mockup_radar",
        ["--realm", "test-realm", "--source-id", "test-source"],
    )

    assert result.returncode != 0


def test_mockup_radar_missing_source_id_arg(run_connector):
    """Test that mockup_radar fails when --source-id is missing."""
    result = run_connector(
        "mockups",
        "mockup_radar",
        ["--realm", "test-realm", "--entity-id", "test-entity"],
    )

    assert result.returncode != 0


def test_mockup_radar_shows_optional_args(run_connector):
    """Test that mockup_radar help documents optional args."""
    result = run_connector("mockups", "mockup_radar", ["--help"])

    assert result.returncode == 0
    # Check that optional args are documented
    assert "--spokes" in result.stdout or "spokes" in result.stdout.lower()


def test_mockup_radar_generates_data(connector_process_factory):
    """Test that mockup_radar runs and generates radar data."""
    # Start mockup_radar with a short duration
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
            "10",
            "--seconds_per_sweep",
            "0.5",
        ],
    )
    radar.start()

    # Give it time to generate some data
    time.sleep(2)

    # Verify it's still running (hasn't crashed)
    assert radar.is_running(), "mockup_radar should be running"

    # Stop it
    radar.stop()


def test_mockup_radar_data_recorded(connector_process_factory, temp_dir: Path):
    """Test that mockup_radar data can be recorded by mcap-record."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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

    # Start radar publisher
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
            "20",
            "--seconds_per_sweep",
            "1",
        ],
    )
    radar.start()

    # Let them run
    time.sleep(3)

    # Stop both
    radar.stop()
    recorder.stop()

    # Verify MCAP file contains radar data
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert (
        file_size > 100
    ), f"MCAP file should contain radar data, got {file_size} bytes"
