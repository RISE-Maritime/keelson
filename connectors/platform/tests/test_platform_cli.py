"""Tests for Platform connector CLI."""

import pytest


class TestPlatformGeometryCli:
    """Tests for platform-geometry2keelson CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("platform", "platform-geometry", ["--help"])
        assert result.returncode == 0
        # Should show help about the platform geometry connector
        assert "platform" in result.stdout.lower() or "geometry" in result.stdout.lower() or "config" in result.stdout.lower()

    def test_requires_config(self, run_connector):
        """Test that config file argument is required or handled."""
        result = run_connector("platform", "platform-geometry", [])
        # Should either show help or error about missing config
        # (depending on whether config is required or has a default)
        assert result.returncode == 0 or "required" in result.stderr.lower() or "config" in result.stderr.lower()
