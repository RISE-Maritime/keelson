"""
End-to-end tests for the klog connector.

Tests recording to klog format and conversion to MCAP.
"""

import time
from pathlib import Path

import pytest
from mcap.reader import make_reader


@pytest.mark.e2e
def test_klog_record_creates_output_file(connector_process_factory, temp_dir: Path):
    """Test that klog-record creates an output file."""
    output_file = temp_dir / "recording.klog"

    recorder = connector_process_factory(
        "klog",
        "klog-record",
        ["--key", "test/**", "--output", str(output_file), "--mode", "peer"],
    )
    recorder.start()
    time.sleep(2)
    recorder.stop()

    assert output_file.exists(), "klog file should be created"


@pytest.mark.e2e
def test_klog_record_with_publisher(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that klog-record captures messages from a publisher."""
    output_file = temp_dir / "recording.klog"

    recorder = connector_process_factory(
        "klog",
        "klog-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output",
            str(output_file),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
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
    publisher.start()
    time.sleep(3)

    publisher.stop()
    recorder.stop()

    assert output_file.exists(), "klog file should be created"
    file_size = output_file.stat().st_size
    assert file_size > 0, f"klog file should contain data, got {file_size} bytes"


@pytest.mark.e2e
def test_klog_record_multiple_topics(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that klog-record captures messages from multiple topics."""
    output_file = temp_dir / "recording.klog"

    recorder = connector_process_factory(
        "klog",
        "klog-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output",
            str(output_file),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    radar1 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "vessel1",
            "--source-id",
            "radar1",
            "--spokes_per_sweep",
            "5",
            "--seconds_per_sweep",
            "0.3",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    radar2 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "vessel2",
            "--source-id",
            "radar2",
            "--spokes_per_sweep",
            "5",
            "--seconds_per_sweep",
            "0.3",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )

    radar1.start()
    radar2.start()
    time.sleep(2)

    radar1.stop()
    radar2.stop()
    recorder.stop()

    assert output_file.exists(), "klog file should be created"
    file_size = output_file.stat().st_size
    assert file_size > 100, "klog file should contain data from both publishers"


@pytest.mark.e2e
def test_klog2mcap_converts_file(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that klog2mcap converts a klog file to MCAP format."""
    klog_file = temp_dir / "recording.klog"
    mcap_file = temp_dir / "output.mcap"

    recorder = connector_process_factory(
        "klog",
        "klog-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output",
            str(klog_file),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
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
    publisher.start()
    time.sleep(2)

    publisher.stop()
    recorder.stop()

    assert klog_file.exists(), "klog file should exist for conversion test"
    assert klog_file.stat().st_size > 0, "klog file should contain data"

    result = run_connector(
        "klog",
        "klog2mcap",
        ["--input", str(klog_file), "--output", str(mcap_file)],
        timeout=30,
    )

    assert (
        result.returncode == 0
    ), f"klog2mcap should complete successfully: {result.stderr}"
    assert mcap_file.exists(), "MCAP output file should be created"
    assert mcap_file.stat().st_size > 500, "MCAP file should contain recorded data"


@pytest.mark.e2e
def test_klog2mcap_preserves_topics(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that klog2mcap preserves all topics from the klog file."""
    klog_file = temp_dir / "recording.klog"
    mcap_file = temp_dir / "output.mcap"

    recorder = connector_process_factory(
        "klog",
        "klog-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output",
            str(klog_file),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
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
    publisher.start()
    time.sleep(2)

    publisher.stop()
    recorder.stop()

    result = run_connector(
        "klog",
        "klog2mcap",
        ["--input", str(klog_file), "--output", str(mcap_file)],
        timeout=30,
    )
    assert result.returncode == 0

    with open(mcap_file, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None, "MCAP should have summary"

        topics = [ch.topic for ch in summary.channels.values()]

        spoke_topics = [t for t in topics if "radar_spoke" in t]
        sweep_topics = [t for t in topics if "radar_sweep" in t]

        assert len(spoke_topics) > 0, "Should have radar_spoke topic"
        assert len(sweep_topics) > 0, "Should have radar_sweep topic"
