"""Tests for Klog connector CLI."""

import pytest


class TestKlogRecordCli:
    """Tests for keelson2klog CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("klog", "klog-record", ["--help"])
        assert result.returncode == 0
        assert "--key" in result.stdout or "-k" in result.stdout

    def test_shows_required_args_error(self, run_connector):
        """Test that missing required args shows error."""
        result = run_connector("klog", "klog-record", [])
        # Should error about missing required arguments
        assert result.returncode != 0


class TestKlog2McapCli:
    """Tests for klog2mcap CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("klog", "klog2mcap", ["--help"])
        assert result.returncode == 0
        assert "klog" in result.stdout.lower() or "mcap" in result.stdout.lower()
