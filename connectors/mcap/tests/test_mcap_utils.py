"""Tests for MCAP connector utility functions."""

import os
import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import pytest

# Add the bin directory to the path so we can import the module
bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

# Import parse_size from the keelson2mcap module
keelson2mcap = import_module("keelson2mcap")
parse_size = keelson2mcap.parse_size
get_disk_free_percent = keelson2mcap.get_disk_free_percent
get_cpu_safeguard_status = keelson2mcap.get_cpu_safeguard_status
_nearest_existing_path = keelson2mcap._nearest_existing_path


class TestParseSize:
    """Tests for the parse_size function."""

    def test_parse_bytes(self):
        """Test parsing plain bytes."""
        assert parse_size("100") == 100
        assert parse_size("100B") == 100
        assert parse_size("100b") == 100

    def test_parse_kilobytes(self):
        """Test parsing kilobytes."""
        assert parse_size("1KB") == 1024
        assert parse_size("1K") == 1024
        assert parse_size("1kb") == 1024
        assert parse_size("10KB") == 10 * 1024

    def test_parse_megabytes(self):
        """Test parsing megabytes."""
        assert parse_size("1MB") == 1024**2
        assert parse_size("1M") == 1024**2
        assert parse_size("100MB") == 100 * (1024**2)
        assert parse_size("500mb") == 500 * (1024**2)

    def test_parse_gigabytes(self):
        """Test parsing gigabytes."""
        assert parse_size("1GB") == 1024**3
        assert parse_size("1G") == 1024**3
        assert parse_size("2GB") == 2 * (1024**3)

    def test_parse_terabytes(self):
        """Test parsing terabytes."""
        assert parse_size("1TB") == 1024**4
        assert parse_size("1T") == 1024**4

    def test_parse_none(self):
        """Test that None input returns None."""
        assert parse_size(None) is None

    def test_parse_with_whitespace(self):
        """Test parsing with surrounding whitespace."""
        assert parse_size("  100MB  ") == 100 * (1024**2)
        assert parse_size(" 1GB ") == 1024**3

    def test_parse_decimal_values(self):
        """Test parsing decimal values."""
        assert parse_size("1.5GB") == int(1.5 * (1024**3))
        assert parse_size("0.5MB") == int(0.5 * (1024**2))

    def test_invalid_format(self):
        """Test that invalid formats raise ValueError."""
        with pytest.raises(ValueError):
            parse_size("invalid")

        with pytest.raises(ValueError):
            parse_size("100XB")

        with pytest.raises(ValueError):
            parse_size("MB100")

    def test_case_insensitive(self):
        """Test that parsing is case insensitive."""
        assert parse_size("100mb") == parse_size("100MB")
        assert parse_size("1gb") == parse_size("1GB")
        assert parse_size("500Kb") == parse_size("500KB")


class TestNearestExistingPath:
    """Tests for _nearest_existing_path."""

    def test_existing_path_returned_as_is(self, tmp_path):
        """An existing path should be returned unchanged (resolved)."""
        result = _nearest_existing_path(tmp_path)
        assert result == tmp_path.resolve()

    def test_nonexistent_child_returns_parent(self, tmp_path):
        """A nonexistent path should return the nearest existing parent."""
        nonexistent = tmp_path / "does_not_exist"
        result = _nearest_existing_path(nonexistent)
        assert result == tmp_path.resolve()

    def test_deeply_nested_nonexistent_returns_nearest_parent(self, tmp_path):
        """A deeply nested nonexistent path should walk up to the nearest existing dir."""
        nonexistent = tmp_path / "a" / "b" / "c" / "d"
        result = _nearest_existing_path(nonexistent)
        assert result == tmp_path.resolve()

    def test_existing_file_returned(self, tmp_path):
        """An existing file should be returned as-is."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = _nearest_existing_path(f)
        assert result == f.resolve()


class TestGetDiskFreePercent:
    """Tests for get_disk_free_percent."""

    def test_returns_tuple_of_three(self, tmp_path):
        """Should return a 3-tuple: (free_percent, free_bytes, total_bytes)."""
        result = get_disk_free_percent(tmp_path)
        assert len(result) == 3

    def test_free_percent_in_valid_range(self, tmp_path):
        """Free percent should be between 0 and 100."""
        free_percent, _, _ = get_disk_free_percent(tmp_path)
        assert 0.0 <= free_percent <= 100.0

    def test_free_bytes_lte_total(self, tmp_path):
        """Free bytes should not exceed total bytes."""
        _, free_bytes, total_bytes = get_disk_free_percent(tmp_path)
        assert free_bytes <= total_bytes

    def test_works_with_nonexistent_subdirectory(self, tmp_path):
        """Should fall back to nearest existing parent for nonexistent paths."""
        nonexistent = tmp_path / "recordings" / "2024"
        free_percent, free_bytes, total_bytes = get_disk_free_percent(nonexistent)
        assert 0.0 <= free_percent <= 100.0
        assert total_bytes > 0

    def test_zero_total_raises_value_error(self, tmp_path):
        """When total disk size is reported as 0, should raise ValueError."""
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = type(
                "usage", (), {"free": 0, "total": 0, "used": 0}
            )()
            with pytest.raises(ValueError, match="degenerate/virtual filesystem"):
                get_disk_free_percent(tmp_path)


class TestGetCpuSafeguardStatus:
    """Tests for get_cpu_safeguard_status."""

    def test_returns_none_on_platform_without_getloadavg(self):
        """Should return None if os.getloadavg is not available."""
        real = getattr(os, "getloadavg", None)
        try:
            if hasattr(os, "getloadavg"):
                delattr(os, "getloadavg")
            result = get_cpu_safeguard_status()
            assert result is None
        finally:
            if real is not None:
                os.getloadavg = real

    @pytest.mark.skipif(
        not hasattr(os, "getloadavg"), reason="no getloadavg on this platform"
    )
    def test_returns_dict_with_expected_keys(self):
        """Should return a dict with all expected keys."""
        result = get_cpu_safeguard_status()
        assert result is not None
        assert "cpu_count" in result
        assert "allowed_load" in result
        assert "load1" in result
        assert "load5" in result
        assert "load15" in result
        assert "overloaded" in result

    @pytest.mark.skipif(
        not hasattr(os, "getloadavg"), reason="no getloadavg on this platform"
    )
    def test_overloaded_false_when_load_is_low(self):
        """Should report not overloaded when load is near zero."""
        with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
            result = get_cpu_safeguard_status()
        assert result is not None
        assert result["overloaded"] is False

    @pytest.mark.skipif(
        not hasattr(os, "getloadavg"), reason="no getloadavg on this platform"
    )
    def test_overloaded_true_when_load_exceeds_threshold(self):
        """Should report overloaded when load1 equals or exceeds allowed_load."""
        cpu_count = max(1, os.cpu_count() or 1)
        # Set load1 far above allowed threshold
        extreme_load = float(cpu_count) * 10.0
        with patch(
            "os.getloadavg", return_value=(extreme_load, extreme_load, extreme_load)
        ):
            result = get_cpu_safeguard_status()
        assert result is not None
        assert result["overloaded"] is True

    @pytest.mark.skipif(
        not hasattr(os, "getloadavg"), reason="no getloadavg on this platform"
    )
    def test_allowed_load_is_positive(self):
        """allowed_load should always be positive even with large reserve_cores."""
        result = get_cpu_safeguard_status(reserve_cores=9999.0)
        assert result is not None
        assert result["allowed_load"] > 0.0

    @pytest.mark.skipif(
        not hasattr(os, "getloadavg"), reason="no getloadavg on this platform"
    )
    def test_negative_reserve_cores_clamped_to_zero(self):
        """Negative reserve_cores should be treated as 0."""
        result_zero = get_cpu_safeguard_status(reserve_cores=0.0)
        result_negative = get_cpu_safeguard_status(reserve_cores=-5.0)
        assert result_zero is not None
        assert result_negative is not None
        assert result_zero["allowed_load"] == result_negative["allowed_load"]
