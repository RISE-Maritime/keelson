"""
End-to-end tests for the klog connector CLIs.

Tests the following commands:
- klog-record: Records Zenoh messages to klog binary format
- klog2mcap: Converts klog files to MCAP format
"""

from pathlib import Path


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
    assert "required" in result.stderr.lower() or "error" in result.stderr.lower()


def test_klog_record_missing_key_arg(run_in_container):
    """Test that klog-record fails when --key is missing."""
    result = run_in_container("klog-record --output /tmp/test.klog")

    assert result.returncode != 0
    assert "key" in result.stderr.lower() or "required" in result.stderr.lower()


def test_klog_record_missing_output_arg(run_in_container):
    """Test that klog-record fails when --output is missing."""
    result = run_in_container("klog-record --key test/key")

    assert result.returncode != 0
    assert "output" in result.stderr.lower() or "required" in result.stderr.lower()


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
    assert "required" in result.stderr.lower() or "error" in result.stderr.lower()


def test_klog2mcap_missing_input_arg(run_in_container):
    """Test that klog2mcap fails when --input is missing."""
    result = run_in_container("klog2mcap --output /tmp/test.mcap")

    assert result.returncode != 0
    assert "input" in result.stderr.lower() or "required" in result.stderr.lower()


def test_klog2mcap_missing_output_arg(run_in_container):
    """Test that klog2mcap fails when --output is missing."""
    result = run_in_container("klog2mcap --input /tmp/test.klog")

    assert result.returncode != 0
    assert "output" in result.stderr.lower() or "required" in result.stderr.lower()


def test_klog2mcap_input_file_not_found(run_in_container, temp_dir: Path):
    """Test that klog2mcap fails gracefully when input file doesn't exist."""
    result = run_in_container(
        "klog2mcap --input /data/nonexistent.klog --output /data/output.mcap",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0
