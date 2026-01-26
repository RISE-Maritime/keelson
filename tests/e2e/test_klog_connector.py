"""
End-to-end tests for the klog connector CLIs.

Tests the following commands:
- klog-record: Records Zenoh messages to klog binary format
- klog2mcap: Converts klog files to MCAP format
"""

import time
from pathlib import Path

from mcap.reader import make_reader


# =============================================================================
# klog-record CLI tests
# =============================================================================


def test_klog_record_help(run_connector):
    """Test that klog-record --help returns successfully."""
    result = run_connector("klog", "klog-record", ["--help"])

    assert result.returncode == 0
    assert "klog-record" in result.stdout
    assert "--key" in result.stdout or "-k" in result.stdout
    assert "--output" in result.stdout or "-o" in result.stdout


def test_klog_record_creates_output_file(connector_process_factory, temp_dir: Path):
    """Test that klog-record creates an output file."""
    output_file = temp_dir / "recording.klog"

    # Start klog-record in peer mode
    recorder = connector_process_factory(
        "klog",
        "klog-record",
        ["--key", "test/**", "--output", str(output_file), "--mode", "peer"],
    )
    recorder.start()

    # Let it run briefly
    time.sleep(2)

    # Stop the recorder
    recorder.stop()

    # Check that a klog file was created
    assert output_file.exists(), "klog file should be created"


def test_klog_record_with_publisher(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that klog-record captures messages from a publisher."""
    output_file = temp_dir / "recording.klog"

    # Start klog-record with explicit listen endpoint
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

    # Give recorder time to initialize
    time.sleep(1)

    # Start mockup_radar with explicit connect endpoint
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

    # Let them run for a bit
    time.sleep(3)

    # Stop both
    publisher.stop()
    recorder.stop()

    # Verify klog file was created with data
    assert output_file.exists(), "klog file should be created"
    file_size = output_file.stat().st_size
    assert file_size > 0, f"klog file should contain data, got {file_size} bytes"


def test_klog_record_multiple_topics(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that klog-record captures messages from multiple topics."""
    output_file = temp_dir / "recording.klog"

    # Start klog-record with multiple key patterns
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

    # Start two radar publishers with different source IDs
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

    # Verify klog file contains data from both publishers
    assert output_file.exists(), "klog file should be created"
    file_size = output_file.stat().st_size
    # With two publishers, file should be larger
    assert file_size > 100, f"klog file should contain data from both publishers"


# =============================================================================
# klog2mcap CLI tests
# =============================================================================


def test_klog2mcap_help(run_connector):
    """Test that klog2mcap --help returns successfully."""
    result = run_connector("klog", "klog2mcap", ["--help"])

    assert result.returncode == 0
    assert "klog2mcap" in result.stdout
    assert "--input" in result.stdout or "-i" in result.stdout
    assert "--output" in result.stdout or "-o" in result.stdout


def test_klog2mcap_converts_file(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that klog2mcap converts a klog file to MCAP format."""
    klog_file = temp_dir / "recording.klog"
    mcap_file = temp_dir / "output.mcap"

    # First, create a klog file with some data
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

    # Publish some data with explicit connection
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

    # Verify klog file exists with data
    assert klog_file.exists(), "klog file should exist for conversion test"
    assert klog_file.stat().st_size > 0, "klog file should contain data"

    # Now convert klog to mcap
    result = run_connector(
        "klog",
        "klog2mcap",
        ["--input", str(klog_file), "--output", str(mcap_file)],
        timeout=30,
    )

    assert (
        result.returncode == 0
    ), f"klog2mcap should complete successfully: {result.stderr}"

    # Verify MCAP file was created with actual data
    assert mcap_file.exists(), "MCAP output file should be created"
    # MCAP header is ~300 bytes, actual data should be more
    assert mcap_file.stat().st_size > 500, "MCAP file should contain recorded data"


def test_klog2mcap_preserves_topics(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that klog2mcap preserves all topics from the klog file."""
    klog_file = temp_dir / "recording.klog"
    mcap_file = temp_dir / "output.mcap"

    # Create a klog file with data from radar (publishes spoke and sweep topics)
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

    # Convert klog to mcap
    result = run_connector(
        "klog",
        "klog2mcap",
        ["--input", str(klog_file), "--output", str(mcap_file)],
        timeout=30,
    )
    assert result.returncode == 0

    # Read MCAP file and verify topics are present
    with open(mcap_file, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None, "MCAP should have summary"

        # Get all channel topics
        topics = [ch.topic for ch in summary.channels.values()]

        # Should have radar_spoke and radar_sweep topics
        spoke_topics = [t for t in topics if "radar_spoke" in t]
        sweep_topics = [t for t in topics if "radar_sweep" in t]

        assert len(spoke_topics) > 0, "Should have radar_spoke topic"
        assert len(sweep_topics) > 0, "Should have radar_sweep topic"
