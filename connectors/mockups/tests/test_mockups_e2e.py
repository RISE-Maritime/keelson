"""
End-to-end tests for the Mockups connector.

Tests the mockup_radar data generation functionality.
"""

import time
from pathlib import Path

import pytest
from mcap.reader import make_reader


@pytest.mark.e2e
def test_mockup_radar_generates_data(connector_process_factory):
    """Test that mockup_radar runs and generates radar data."""
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
    time.sleep(2)

    assert radar.is_running(), "mockup_radar should be running"
    radar.stop()


@pytest.mark.e2e
def test_mockup_radar_data_recorded(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mockup_radar data can be recorded by mcap-record."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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
    time.sleep(3)

    radar.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert (
        file_size > 500
    ), f"MCAP file should contain radar data, got {file_size} bytes"


@pytest.mark.e2e
def test_mockup_radar_publishes_both_topics(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mockup_radar publishes both radar_spoke and radar_sweep topics."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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
    time.sleep(2)

    radar.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        topics = [ch.topic for ch in summary.channels.values()]

        spoke_topics = [t for t in topics if "radar_spoke" in t]
        sweep_topics = [t for t in topics if "radar_sweep" in t]

        assert len(spoke_topics) > 0, "Should have radar_spoke topic"
        assert len(sweep_topics) > 0, "Should have radar_sweep topic"


@pytest.mark.e2e
def test_mockup_radar_configurable_parameters(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mockup_radar respects configurable parameters."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

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

    spokes_per_sweep = 5
    seconds_per_sweep = 0.25

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
    time.sleep(2)

    radar.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        if summary.statistics:
            total_messages = sum(summary.statistics.channel_message_counts.values())
            assert (
                total_messages > 20
            ), f"Expected multiple sweeps, got {total_messages} messages"
