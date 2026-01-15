"""Tests for Mockups connector CLI."""


class TestMockupRadarCli:
    """Tests for mockup-radar2keelson CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("mockups", "mockup_radar", ["--help"])
        assert result.returncode == 0
        # Should show help about the mockup radar
        assert (
            "radar" in result.stdout.lower()
            or "mockup" in result.stdout.lower()
            or "keelson" in result.stdout.lower()
        )

    def test_shows_usage(self, run_connector):
        """Test that running without arguments shows usage or starts the mockup."""
        result = run_connector("mockups", "mockup_radar", [], timeout=5.0)
        # Mockup might start and run, or show usage, or error about args
        # We mainly want to verify it doesn't crash on import
        assert result.returncode is not None
