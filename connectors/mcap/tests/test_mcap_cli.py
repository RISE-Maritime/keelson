"""Tests for MCAP connector CLI."""


class TestMcapRecordCli:
    """Tests for mcap-record (keelson2mcap.py) CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("mcap", "mcap-record", ["--help"])
        assert result.returncode == 0
        assert "mcap-record" in result.stdout
        assert "--output-folder" in result.stdout
        assert "--key" in result.stdout

    def test_requires_key_argument(self, run_connector):
        """Test that -k/--key is required."""
        result = run_connector("mcap", "mcap-record", ["--output-folder", "/tmp"])
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "key" in result.stderr.lower()

    def test_requires_output_folder(self, run_connector):
        """Test that --output-folder is required."""
        result = run_connector("mcap", "mcap-record", ["--key", "test/**"])
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "output" in result.stderr.lower()

    def test_mode_choices(self, run_connector):
        """Test that --mode only accepts peer or client."""
        result = run_connector(
            "mcap",
            "mcap-record",
            ["--key", "test/**", "--output-folder", "/tmp", "--mode", "invalid"],
        )
        assert result.returncode != 0
        assert "invalid" in result.stderr.lower() or "choice" in result.stderr.lower()

    def test_rotate_when_choices(self, run_connector):
        """Test that --rotate-when accepts valid choices."""
        result = run_connector("mcap", "mcap-record", ["--help"])
        assert "--rotate-when" in result.stdout
        # Check that valid choices are documented
        assert "H" in result.stdout  # hourly
        assert "D" in result.stdout  # daily


class TestMcapReplayCli:
    """Tests for mcap-replay (mcap2keelson.py) CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("mcap", "mcap-replay", ["--help"])
        assert result.returncode == 0
        assert "mcap-replay" in result.stdout
        assert "--mcap-file" in result.stdout

    def test_requires_mcap_file(self, run_connector):
        """Test that --mcap-file is required."""
        result = run_connector("mcap", "mcap-replay", [])
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "mcap" in result.stderr.lower()


class TestMcapTaggCli:
    """Tests for mcap-tagg CLI."""

    def test_help_output(self, run_connector):
        """Test that --help works and returns expected output."""
        result = run_connector("mcap", "mcap-tagg", ["--help"])
        assert result.returncode == 0
        assert "mcap-tagg" in result.stdout
        assert "--input-dir" in result.stdout

    def test_requires_input_dir(self, run_connector):
        """Test that --input-dir is required."""
        result = run_connector("mcap", "mcap-tagg", [])
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "input" in result.stderr.lower()
