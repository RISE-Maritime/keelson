"""Tests for the LabJack connector CLI."""


class TestLabjackCli:
    """Tests for labjack2keelson CLI."""

    def test_help_output(self, run_connector):
        result = run_connector("labjack", "labjack", ["--help"])
        assert result.returncode == 0
        assert "labjack" in result.stdout.lower() or "voltage" in result.stdout.lower()

    def test_requires_realm(self, run_connector):
        result = run_connector(
            "labjack",
            "labjack",
            ["--entity-id", "rov", "--config", "x.json"],
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower()

    def test_requires_entity_id(self, run_connector):
        result = run_connector(
            "labjack",
            "labjack",
            ["--realm", "rise", "--config", "x.json"],
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower()

    def test_requires_config(self, run_connector):
        result = run_connector(
            "labjack",
            "labjack",
            ["--realm", "rise", "--entity-id", "rov"],
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower()
