"""
End-to-end tests for the klog connector CLIs.

Tests the following commands:
- klog-record: Records Zenoh messages to klog binary format
- klog2mcap: Converts klog files to MCAP format
"""

import time
from pathlib import Path


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


def test_klog_record_missing_required_args(run_connector):
    """Test that klog-record fails gracefully when required args are missing."""
    result = run_connector("klog", "klog-record", [])

    assert result.returncode != 0


def test_klog_record_missing_key_arg(run_connector):
    """Test that klog-record fails when --key is missing."""
    result = run_connector("klog", "klog-record", ["--output", "/tmp/test.klog"])

    assert result.returncode != 0


def test_klog_record_missing_output_arg(run_connector):
    """Test that klog-record fails when --output is missing."""
    result = run_connector("klog", "klog-record", ["--key", "test/key"])

    assert result.returncode != 0


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


def test_klog2mcap_missing_required_args(run_connector):
    """Test that klog2mcap fails gracefully when required args are missing."""
    result = run_connector("klog", "klog2mcap", [])

    assert result.returncode != 0


def test_klog2mcap_missing_input_arg(run_connector):
    """Test that klog2mcap fails when --input is missing."""
    result = run_connector("klog", "klog2mcap", ["--output", "/tmp/test.mcap"])

    assert result.returncode != 0


def test_klog2mcap_missing_output_arg(run_connector):
    """Test that klog2mcap fails when --output is missing."""
    result = run_connector("klog", "klog2mcap", ["--input", "/tmp/test.klog"])

    assert result.returncode != 0


def test_klog2mcap_input_file_not_found(run_connector, temp_dir: Path):
    """Test that klog2mcap fails gracefully when input file doesn't exist."""
    result = run_connector(
        "klog",
        "klog2mcap",
        [
            "--input",
            str(temp_dir / "nonexistent.klog"),
            "--output",
            str(temp_dir / "output.mcap"),
        ],
    )

    assert result.returncode != 0


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
