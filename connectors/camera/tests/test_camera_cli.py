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
        for flag in ("--width", "--height", "--fps", "--fourcc"):
            assert flag in result.stdout

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

    @staticmethod
    def _base():
        return ["--realm", "r", "--entity-id", "e", "--source-id", "s"] + [
            "--camera-url",
            "u",
        ]

    def test_width_needs_height(self, run_connector):
        """--width alone is refused: a size is both dimensions."""
        result = run_connector("camera", "camera", self._base() + ["--width", "1920"])
        assert result.returncode != 0
        assert "--width and --height go together" in result.stderr

    def test_height_needs_width(self, run_connector):
        """--height alone is refused too."""
        result = run_connector("camera", "camera", self._base() + ["--height", "1080"])
        assert result.returncode != 0
        assert "--width and --height go together" in result.stderr

    def test_fourcc_is_four_characters(self, run_connector):
        """A FourCC that is not four characters is refused."""
        result = run_connector("camera", "camera", self._base() + ["--fourcc", "MJPEG"])
        assert result.returncode != 0
        assert "FourCC" in result.stderr

    def test_fps_must_be_positive(self, run_connector):
        """A zero frame rate is refused."""
        result = run_connector("camera", "camera", self._base() + ["--fps", "0"])
        assert result.returncode != 0
        assert "positive" in result.stderr

    def test_width_must_be_positive(self, run_connector):
        """A negative width is refused."""
        result = run_connector(
            "camera", "camera", self._base() + ["--width", "-1", "--height", "1080"]
        )
        assert result.returncode != 0
