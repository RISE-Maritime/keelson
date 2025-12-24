"""
End-to-end tests for the MCAP connector CLIs.

Tests the following commands:
- mcap-record: Records Zenoh messages to MCAP format
- mcap-replay: Replays MCAP files to Zenoh
- mcap-tagg: Post-processes MCAP files with annotations
"""

import time
from pathlib import Path

from conftest import wait_for_file


# =============================================================================
# mcap-record CLI tests
# =============================================================================


def test_mcap_record_help(run_in_container):
    """Test that mcap-record --help returns successfully."""
    result = run_in_container("mcap-record --help")

    assert result.returncode == 0
    assert "mcap-record" in result.stdout
    assert "--key" in result.stdout or "-k" in result.stdout
    assert "--output-folder" in result.stdout


def test_mcap_record_missing_required_args(run_in_container):
    """Test that mcap-record fails gracefully when required args are missing."""
    result = run_in_container("mcap-record")

    assert result.returncode != 0


def test_mcap_record_missing_key_arg(run_in_container):
    """Test that mcap-record fails when --key is missing."""
    result = run_in_container("mcap-record --output-folder /tmp")

    assert result.returncode != 0


def test_mcap_record_missing_output_folder_arg(run_in_container):
    """Test that mcap-record fails when --output-folder is missing."""
    result = run_in_container("mcap-record --key test/key")

    assert result.returncode != 0


def test_mcap_record_creates_output_file(
    container_factory, docker_network, temp_dir: Path
):
    """Test that mcap-record creates an MCAP output file."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()
    output_dir.chmod(0o777)

    # Start mcap-record with a timeout - it will run until interrupted
    # Using bash timeout to limit execution time
    recorder = container_factory(
        command=(
            "timeout --signal=INT 3 "
            "mcap-record --key 'test/**' --output-folder /data "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(output_dir): "/data"},
    )
    recorder.start()

    # Wait for the recorder to finish (timeout will stop it)
    exit_code = recorder.wait(timeout=10)

    # Check that an MCAP file was created
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    # Verify the file has content (at least MCAP header)
    mcap_file = mcap_files[0]
    assert mcap_file.stat().st_size > 0, "MCAP file should not be empty"


def test_mcap_record_with_publisher(container_factory, docker_network, temp_dir: Path):
    """Test that mcap-record captures messages from a publisher."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()
    output_dir.chmod(0o777)

    # Start mcap-record first
    recorder = container_factory(
        command=(
            "timeout --signal=INT 5 "
            "mcap-record --key 'test-realm/**' --output-folder /data "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(output_dir): "/data"},
    )
    recorder.start()

    # Give recorder time to initialize
    time.sleep(1)

    # Start mockup_radar to publish some data (very short duration)
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

    # Verify MCAP file was created
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    # The file should contain recorded data
    mcap_file = mcap_files[0]
    file_size = mcap_file.stat().st_size
    assert file_size > 100, f"MCAP file should contain data, got {file_size} bytes"


# =============================================================================
# mcap-replay CLI tests
# =============================================================================


def test_mcap_replay_help(run_in_container):
    """Test that mcap-replay --help returns successfully."""
    result = run_in_container("mcap-replay --help")

    assert result.returncode == 0
    assert "mcap-replay" in result.stdout
    assert "--mcap-file" in result.stdout or "-mf" in result.stdout


def test_mcap_replay_missing_required_args(run_in_container):
    """Test that mcap-replay fails gracefully when required args are missing."""
    result = run_in_container("mcap-replay")

    assert result.returncode != 0


def test_mcap_replay_file_not_found(run_in_container, temp_dir: Path):
    """Test that mcap-replay fails gracefully when file doesn't exist."""
    result = run_in_container(
        "mcap-replay --mcap-file /nonexistent/file.mcap",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0


# =============================================================================
# mcap-tagg CLI tests
# =============================================================================


def test_mcap_tagg_help(run_in_container):
    """Test that mcap-tagg --help returns successfully."""
    result = run_in_container("mcap-tagg --help")

    assert result.returncode == 0
    assert "mcap-tagg" in result.stdout
    assert "--input-dir" in result.stdout or "-id" in result.stdout


def test_mcap_tagg_missing_required_args(run_in_container):
    """Test that mcap-tagg fails gracefully when required args are missing."""
    result = run_in_container("mcap-tagg")

    assert result.returncode != 0


def test_mcap_tagg_with_empty_directory(run_in_container, temp_dir: Path):
    """Test that mcap-tagg handles an empty directory gracefully."""
    result = run_in_container(
        "mcap-tagg --input-dir /data",
        volumes={str(temp_dir): "/data"},
    )

    # Should succeed but process no files
    assert result.returncode == 0
