"""Tests for MCAP connector utility functions."""

import sys
from importlib import import_module
from pathlib import Path

import pytest

# Add the bin directory to the path so we can import the module
bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

# Import parse_size from the keelson2mcap module
keelson2mcap = import_module("keelson2mcap")
parse_size = keelson2mcap.parse_size


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
