"""
End-to-end tests for the MCAP connector CLIs.

Tests the following commands:
- mcap-record: Records Zenoh messages to MCAP format
- mcap-replay: Replays MCAP files to Zenoh
- mcap-tagg: Post-processes MCAP files with annotations
"""

from pathlib import Path


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
