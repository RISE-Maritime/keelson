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


def test_klog_record_help(run_in_container):
    """Test that klog-record --help returns successfully."""
    result = run_in_container("klog-record --help")

    assert result.returncode == 0
    assert "klog-record" in result.stdout
    assert "--key" in result.stdout or "-k" in result.stdout
    assert "--output" in result.stdout or "-o" in result.stdout


def test_klog_record_missing_required_args(run_in_container):
    """Test that klog-record fails gracefully when required args are missing."""
    result = run_in_container("klog-record")

    assert result.returncode != 0


def test_klog_record_missing_key_arg(run_in_container):
    """Test that klog-record fails when --key is missing."""
    result = run_in_container("klog-record --output /tmp/test.klog")

    assert result.returncode != 0


def test_klog_record_missing_output_arg(run_in_container):
    """Test that klog-record fails when --output is missing."""
    result = run_in_container("klog-record --key test/key")

    assert result.returncode != 0


def test_klog_record_creates_output_file(
    container_factory, docker_network, temp_dir: Path
):
    """Test that klog-record creates an output file."""
    output_dir = temp_dir / "klog_output"
    output_dir.mkdir()
    output_dir.chmod(0o777)

    # Start klog-record with a timeout
    recorder = container_factory(
        command=(
            "timeout --signal=INT 3 "
            "klog-record --key 'test/**' --output /data/recording.klog "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(output_dir): "/data"},
    )
    recorder.start()

    # Wait for the recorder to finish
    recorder.wait(timeout=10)

    # Check that a klog file was created
    klog_file = output_dir / "recording.klog"
    assert klog_file.exists(), "klog file should be created"


def test_klog_record_with_publisher(container_factory, docker_network, temp_dir: Path):
    """Test that klog-record captures messages from a publisher."""
    output_dir = temp_dir / "klog_output"
    output_dir.mkdir()
    output_dir.chmod(0o777)

    # Start klog-record first
    recorder = container_factory(
        command=(
            "timeout --signal=INT 5 "
            "klog-record --key 'test-realm/**' --output /data/recording.klog "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(output_dir): "/data"},
    )
    recorder.start()

    # Give recorder time to initialize
    time.sleep(1)

    # Start mockup_radar to publish some data
    publisher = container_factory(
        command=(
            "timeout --signal=INT 2 "
            "mockup_radar --realm test-realm --entity-id test-vessel "
            "--source-id radar1 --spokes_per_sweep 10 --seconds_per_sweep 0.5"
        ),
        network=docker_network.name,
    )
    publisher.start()

    # Wait for both to finish
    publisher.wait(timeout=10)
    recorder.wait(timeout=10)

    # Verify klog file was created with data
    klog_file = output_dir / "recording.klog"
    assert klog_file.exists(), "klog file should be created"
    file_size = klog_file.stat().st_size
    assert file_size > 0, f"klog file should contain data, got {file_size} bytes"


# =============================================================================
# klog2mcap CLI tests
# =============================================================================


def test_klog2mcap_help(run_in_container):
    """Test that klog2mcap --help returns successfully."""
    result = run_in_container("klog2mcap --help")

    assert result.returncode == 0
    assert "klog2mcap" in result.stdout
    assert "--input" in result.stdout or "-i" in result.stdout
    assert "--output" in result.stdout or "-o" in result.stdout


def test_klog2mcap_missing_required_args(run_in_container):
    """Test that klog2mcap fails gracefully when required args are missing."""
    result = run_in_container("klog2mcap")

    assert result.returncode != 0


def test_klog2mcap_missing_input_arg(run_in_container):
    """Test that klog2mcap fails when --input is missing."""
    result = run_in_container("klog2mcap --output /tmp/test.mcap")

    assert result.returncode != 0


def test_klog2mcap_missing_output_arg(run_in_container):
    """Test that klog2mcap fails when --output is missing."""
    result = run_in_container("klog2mcap --input /tmp/test.klog")

    assert result.returncode != 0


def test_klog2mcap_input_file_not_found(run_in_container, temp_dir: Path):
    """Test that klog2mcap fails gracefully when input file doesn't exist."""
    result = run_in_container(
        "klog2mcap --input /data/nonexistent.klog --output /data/output.mcap",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0


def test_klog2mcap_converts_file(container_factory, docker_network, temp_dir: Path):
    """Test that klog2mcap converts a klog file to MCAP format."""
    data_dir = temp_dir / "data"
    data_dir.mkdir()
    data_dir.chmod(0o777)

    # First, create a klog file with some data
    recorder = container_factory(
        command=(
            "timeout --signal=INT 4 "
            "klog-record --key 'test-realm/**' --output /data/recording.klog "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(data_dir): "/data"},
    )
    recorder.start()

    time.sleep(1)

    # Publish some data
    publisher = container_factory(
        command=(
            "timeout --signal=INT 2 "
            "mockup_radar --realm test-realm --entity-id test-vessel "
            "--source-id radar1 --spokes_per_sweep 10 --seconds_per_sweep 0.5"
        ),
        network=docker_network.name,
    )
    publisher.start()

    publisher.wait(timeout=10)
    recorder.wait(timeout=10)

    # Verify klog file exists
    klog_file = data_dir / "recording.klog"
    assert klog_file.exists(), "klog file should exist for conversion test"

    # Now convert klog to mcap
    converter = container_factory(
        command="klog2mcap --input /data/recording.klog --output /data/output.mcap",
        volumes={str(data_dir): "/data"},
    )
    converter.start()
    exit_code = converter.wait(timeout=30)

    assert exit_code == 0, "klog2mcap should complete successfully"

    # Verify MCAP file was created
    mcap_file = data_dir / "output.mcap"
    assert mcap_file.exists(), "MCAP output file should be created"
    assert mcap_file.stat().st_size > 0, "MCAP file should contain data"
