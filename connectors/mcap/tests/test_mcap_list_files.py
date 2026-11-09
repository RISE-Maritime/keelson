"""list_files must not report an unreadable base directory as an empty one (#334)."""

import os
import sys
from importlib import import_module
from pathlib import Path

import pytest

bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

mcap2keelson = import_module("mcap2keelson")
ErrorResponse = mcap2keelson.ErrorResponse
_probe_base_directory = mcap2keelson._probe_base_directory


@pytest.mark.unit
def test_readable_directory_probes_clean(tmp_path):
    assert _probe_base_directory(tmp_path) is None


@pytest.mark.unit
def test_missing_directory_is_not_found(tmp_path):
    missing = tmp_path / "not-mounted"
    description, code = _probe_base_directory(missing)
    assert code == ErrorResponse.Code.NOT_FOUND
    assert str(missing) in description


@pytest.mark.unit
def test_file_in_place_of_directory_is_not_found(tmp_path):
    a_file = tmp_path / "recording.mcap"
    a_file.write_bytes(b"")
    _, code = _probe_base_directory(a_file)
    assert code == ErrorResponse.Code.NOT_FOUND


@pytest.mark.unit
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 directory anyway",
)
def test_unreadable_directory_is_io_failure(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        description, code = _probe_base_directory(locked)
    finally:
        locked.chmod(0o755)
    assert code == ErrorResponse.Code.IO_FAILURE
    assert "not readable" in description
