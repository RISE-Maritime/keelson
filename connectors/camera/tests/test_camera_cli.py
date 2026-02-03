"""Tests for camera connector CLI argument validation."""


class TestCameraCli:
    """Tests for camera2keelson CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("camera", "camera", ["--help"])
        assert result.returncode == 0
        assert "camera2keelson" in result.stdout
        assert "--realm" in result.stdout
        assert "--entity-id" in result.stdout
        assert "--source-id" in result.stdout
        assert "--camera-url" in result.stdout

    def test_requires_realm(self, run_connector):
        """Test that --realm is required."""
        result = run_connector(
            "camera",
            "camera",
            ["--entity-id", "e", "--source-id", "s", "--camera-url", "u"],
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "realm" in result.stderr.lower()

    def test_requires_entity_id(self, run_connector):
        """Test that --entity-id is required."""
        result = run_connector(
            "camera",
            "camera",
            ["--realm", "r", "--source-id", "s", "--camera-url", "u"],
        )
        assert result.returncode != 0
        assert (
            "required" in result.stderr.lower() or "entity-id" in result.stderr.lower()
        )

    def test_requires_source_id(self, run_connector):
        """Test that --source-id is required."""
        result = run_connector(
            "camera",
            "camera",
            ["--realm", "r", "--entity-id", "e", "--camera-url", "u"],
        )
        assert result.returncode != 0
        assert (
            "required" in result.stderr.lower() or "source-id" in result.stderr.lower()
        )

    def test_requires_camera_url(self, run_connector):
        """Test that --camera-url is required."""
        result = run_connector(
            "camera",
            "camera",
            ["--realm", "r", "--entity-id", "e", "--source-id", "s"],
        )
        assert result.returncode != 0
        assert (
            "required" in result.stderr.lower() or "camera-url" in result.stderr.lower()
        )

    def test_send_invalid_choice(self, run_connector):
        """Test that --send rejects invalid choices."""
        result = run_connector(
            "camera",
            "camera",
            [
                "--realm",
                "r",
                "--entity-id",
                "e",
                "--source-id",
                "s",
                "--camera-url",
                "u",
                "--send",
                "invalid",
            ],
        )
        assert result.returncode != 0
        assert "invalid" in result.stderr.lower() or "choice" in result.stderr.lower()
