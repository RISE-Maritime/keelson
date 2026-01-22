"""Tests for Foxglove connector CLI."""


class TestFoxgloveCli:
    """Tests for keelson2foxglove CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("foxglove", "foxglove-liveview", ["--help"])
        assert result.returncode == 0
        # The script should show help output
        assert "keelson" in result.stdout.lower() or "foxglove" in result.stdout.lower()

    def test_shows_usage(self, run_connector):
        """Test that running without arguments shows usage."""
        result = run_connector("foxglove", "foxglove-liveview", [])
        # Should either show help or error about missing required args
        assert (
            result.returncode == 0
            or "required" in result.stderr.lower()
            or "usage" in result.stdout.lower()
        )
