"""
End-to-end tests for the Mockups connector CLI.

Tests the following command:
- mockup_radar: Generates mock radar data for testing
"""

import time
from pathlib import Path

from mcap.reader import make_reader


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


def test_mockup_radar_data_recorded(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mockup_radar data can be recorded by mcap-record."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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

    # Start radar publisher with explicit connect endpoint
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
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    radar.start()

    # Let them run
    time.sleep(3)

    # Stop both
    radar.stop()
    recorder.stop()

    # Verify MCAP file contains radar data (> 500 bytes, not just empty header)
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert (
        file_size > 500
    ), f"MCAP file should contain radar data, got {file_size} bytes"


def test_mockup_radar_publishes_both_topics(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mockup_radar publishes both radar_spoke and radar_sweep topics."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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

    # Start radar with enough time for a complete sweep
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
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    radar.start()

    # Run long enough for multiple sweeps
    time.sleep(2)

    radar.stop()
    recorder.stop()

    # Read MCAP and verify both topic types are present
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        topics = [ch.topic for ch in summary.channels.values()]

        # Should have both radar_spoke and radar_sweep topics
        spoke_topics = [t for t in topics if "radar_spoke" in t]
        sweep_topics = [t for t in topics if "radar_sweep" in t]

        assert len(spoke_topics) > 0, "Should have radar_spoke topic"
        assert len(sweep_topics) > 0, "Should have radar_sweep topic"


def test_mockup_radar_configurable_parameters(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mockup_radar respects configurable parameters."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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

    # Start radar with specific parameters
    spokes_per_sweep = 5
    seconds_per_sweep = 0.25  # Fast sweep

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
            str(spokes_per_sweep),
            "--seconds_per_sweep",
            str(seconds_per_sweep),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    radar.start()

    # Run for 2 seconds - should get approximately 8 sweeps (2 / 0.25)
    time.sleep(2)

    radar.stop()
    recorder.stop()

    # Verify we got multiple sweeps worth of data
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        # Count messages per topic
        # channel_message_counts is Dict[int, int] where values are message counts
        if summary.statistics:
            total_messages = sum(summary.statistics.channel_message_counts.values())
            # With 5 spokes + 1 sweep per 0.25s over 2s, expect ~40+ messages
            assert total_messages > 20, f"Expected multiple sweeps, got {total_messages} messages"
