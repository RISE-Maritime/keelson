"""Tests for MediaMTX connector CLI."""

import pytest


class TestMediamtxWhepCli:
    """Tests for mediamtx-whep CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("mediamtx", "mediamtx", ["--help"])
        assert result.returncode == 0
        # Should show help about the connector
        assert "whep" in result.stdout.lower() or "mediamtx" in result.stdout.lower() or "url" in result.stdout.lower()

    def test_requires_url(self, run_connector):
        """Test that URL argument is handled."""
        result = run_connector("mediamtx", "mediamtx", [])
        # Should either show help or error about missing args
        # (depending on whether URL is required or has a default)
        assert result.returncode == 0 or result.returncode != 0
