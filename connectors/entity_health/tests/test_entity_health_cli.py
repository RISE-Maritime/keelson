"""CLI smoke tests for the entity_health connector."""


class TestEntityHealthCli:
    def test_help_output(self, run_connector):
        result = run_connector("entity_health", "entity_health2keelson", ["--help"])
        assert result.returncode == 0
        assert "entity_health" in result.stdout
        assert "--config" in result.stdout
        assert "--realm" in result.stdout

    def test_requires_config(self, run_connector):
        result = run_connector(
            "entity_health",
            "entity_health2keelson",
            ["--realm", "r", "--entity-id", "e", "--source-id", "s"],
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "config" in result.stderr.lower()

    def test_invalid_config_file(self, run_connector, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        result = run_connector(
            "entity_health",
            "entity_health2keelson",
            [
                "--realm",
                "r",
                "--entity-id",
                "e",
                "--source-id",
                "s",
                "--config",
                str(bad),
            ],
        )
        assert result.returncode != 0
        # The connector logs "Config file is not valid JSON" and exits 1 — pin
        # that path so an unrelated import/startup error doesn't pass silently.
        combined = (result.stderr + result.stdout).lower()
        assert "json" in combined, f"expected JSON error in output, got: {combined!r}"
