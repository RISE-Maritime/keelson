"""Tests for screen connector CLI argument validation."""

BASE = ["--realm", "r", "--entity-id", "e", "--source-id", "s"]


class TestScreenCli:
    """Tests for screen2keelson CLI."""

    def test_help_output(self, run_connector):
        result = run_connector("screen", "screen", ["--help"])
        assert result.returncode == 0
        assert "screen2keelson" in result.stdout
        for flag in (
            "--realm",
            "--entity-id",
            "--source-id",
            "--monitor",
            "--window",
            "--window-id",
            "--region",
            "--fps",
            "--jpeg-quality",
            "--max-width",
            "--list-monitors",
            "--list-windows",
        ):
            assert flag in result.stdout

    def test_requires_realm(self, run_connector):
        result = run_connector(
            "screen", "screen", ["--entity-id", "e", "--source-id", "s"]
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower()
        assert "realm" in result.stderr.lower()

    def test_requires_entity_id(self, run_connector):
        result = run_connector("screen", "screen", ["--realm", "r", "--source-id", "s"])
        assert result.returncode != 0
        assert "entity-id" in result.stderr.lower()

    def test_requires_source_id(self, run_connector):
        result = run_connector("screen", "screen", ["--realm", "r", "--entity-id", "e"])
        assert result.returncode != 0
        assert "source-id" in result.stderr.lower()

    def test_monitor_and_window_are_exclusive(self, run_connector):
        result = run_connector(
            "screen", "screen", BASE + ["--monitor", "1", "--window", "x"]
        )
        assert result.returncode != 0
        assert "not allowed" in result.stderr.lower()

    def test_window_and_window_id_are_exclusive(self, run_connector):
        result = run_connector(
            "screen", "screen", BASE + ["--window", "x", "--window-id", "3"]
        )
        assert result.returncode != 0
        assert "not allowed" in result.stderr.lower()

    def test_bad_region_rejected(self, run_connector):
        result = run_connector("screen", "screen", BASE + ["--region", "1,2,3"])
        assert result.returncode != 0
        assert "region" in result.stderr.lower()

    def test_jpeg_quality_out_of_range(self, run_connector):
        result = run_connector("screen", "screen", BASE + ["--jpeg-quality", "0"])
        assert result.returncode != 0
        assert "jpeg-quality" in result.stderr.lower()

    def test_fps_must_be_positive(self, run_connector):
        result = run_connector("screen", "screen", BASE + ["--fps", "0"])
        assert result.returncode != 0
        assert "--fps" in result.stderr.lower()
