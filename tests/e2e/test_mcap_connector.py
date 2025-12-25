"""
End-to-end tests for the MCAP connector CLIs.

Tests the following commands:
- mcap-record: Records Zenoh messages to MCAP format
- mcap-replay: Replays MCAP files to Zenoh
- mcap-tagg: Post-processes MCAP files with annotations
"""

import json
import time
from pathlib import Path

from mcap.reader import make_reader


# =============================================================================
# mcap-record CLI tests
# =============================================================================


def test_mcap_record_help(run_connector):
    """Test that mcap-record --help returns successfully."""
    result = run_connector("mcap", "mcap-record", ["--help"])

    assert result.returncode == 0
    assert "mcap-record" in result.stdout
    assert "--key" in result.stdout or "-k" in result.stdout
    assert "--output-folder" in result.stdout


def test_mcap_record_creates_output_file(connector_process_factory, temp_dir: Path):
    """Test that mcap-record creates an MCAP output file."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # Start mcap-record in peer mode
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        ["--key", "test/**", "--output-folder", str(output_dir), "--mode", "peer"],
    )
    recorder.start()

    # Let it run briefly to create the file
    time.sleep(2)

    # Stop the recorder
    recorder.stop()

    # Check that an MCAP file was created
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    # Verify the file has content (at least MCAP header)
    mcap_file = mcap_files[0]
    assert mcap_file.stat().st_size > 0, "MCAP file should not be empty"


def test_mcap_record_with_publisher(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record captures messages from a publisher."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # Start mcap-record with explicit listen endpoint
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()

    # Give recorder time to initialize
    time.sleep(1)

    # Start mockup_radar with explicit connect endpoint
    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "radar1",
            "--spokes_per_sweep",
            "10",
            "--seconds_per_sweep",
            "0.5",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    publisher.start()

    # Let them run for a bit
    time.sleep(3)

    # Stop both
    publisher.stop()
    recorder.stop()

    # Verify MCAP file was created
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    # The file should contain recorded data (> 500 bytes, not just empty MCAP header)
    mcap_file = mcap_files[0]
    file_size = mcap_file.stat().st_size
    assert file_size > 500, f"MCAP file should contain data, got {file_size} bytes"


def test_mcap_record_multiple_publishers(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record captures data from multiple publishers."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # Start mcap-record
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    # Start two different publishers
    radar1 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "vessel1",
            "--source-id",
            "radar1",
            "--spokes_per_sweep",
            "5",
            "--seconds_per_sweep",
            "0.3",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    radar2 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "vessel2",
            "--source-id",
            "radar2",
            "--spokes_per_sweep",
            "5",
            "--seconds_per_sweep",
            "0.3",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )

    radar1.start()
    radar2.start()
    time.sleep(2)

    radar1.stop()
    radar2.stop()
    recorder.stop()

    # Verify MCAP file was created with data from both publishers
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    # Read MCAP and verify multiple channels
    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        # Should have channels for both vessel1 and vessel2
        topics = [ch.topic for ch in summary.channels.values()]
        vessel1_topics = [t for t in topics if "vessel1" in t]
        vessel2_topics = [t for t in topics if "vessel2" in t]

        assert len(vessel1_topics) > 0, "Should have topics from vessel1"
        assert len(vessel2_topics) > 0, "Should have topics from vessel2"


# =============================================================================
# mcap-replay CLI tests
# =============================================================================


def test_mcap_replay_help(run_connector):
    """Test that mcap-replay --help returns successfully."""
    result = run_connector("mcap", "mcap-replay", ["--help"])

    assert result.returncode == 0
    assert "mcap-replay" in result.stdout
    assert "--mcap-file" in result.stdout or "-mf" in result.stdout


def test_mcap_replay_starts_successfully(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-replay can read and start replaying an MCAP file."""
    record_dir = temp_dir / "record"
    record_dir.mkdir()

    # Step 1: Record some data
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(record_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "radar1",
            "--spokes_per_sweep",
            "10",
            "--seconds_per_sweep",
            "0.5",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(2)

    publisher.stop()
    recorder.stop()

    # Get the recorded MCAP file
    mcap_files = list(record_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    original_mcap = mcap_files[0]

    # Verify original has data
    with open(original_mcap, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        # channel_message_counts is Dict[int, int] where values are message counts
        original_message_count = (
            sum(summary.statistics.channel_message_counts.values())
            if summary.statistics
            else 0
        )

    assert original_message_count > 0, "Original recording should have messages"

    # Step 2: Start replay and verify it doesn't crash
    replayer = connector_process_factory(
        "mcap",
        "mcap-replay",
        [
            "--mcap-file",
            str(original_mcap),
            "--mode",
            "peer",
        ],
    )
    replayer.start()

    # Give it time to start replaying
    time.sleep(2)

    # The replayer should either still be running or have completed successfully
    # (it exits after replaying all messages)
    replayer.stop()


# =============================================================================
# mcap-tagg CLI tests
# =============================================================================


def test_mcap_tagg_help(run_connector):
    """Test that mcap-tagg --help returns successfully."""
    result = run_connector("mcap", "mcap-tagg", ["--help"])

    assert result.returncode == 0
    assert "mcap-tagg" in result.stdout
    assert "--input-dir" in result.stdout or "-id" in result.stdout


def test_mcap_tagg_with_empty_directory(run_connector, temp_dir: Path):
    """Test that mcap-tagg handles an empty directory gracefully."""
    result = run_connector("mcap", "mcap-tagg", ["--input-dir", str(temp_dir)])

    # Should succeed but process no files
    assert result.returncode == 0


def test_mcap_tagg_processes_files(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-tagg processes MCAP files."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # First, create an MCAP file with data
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "radar1",
            "--spokes_per_sweep",
            "10",
            "--seconds_per_sweep",
            "0.5",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(2)

    publisher.stop()
    recorder.stop()

    # Verify MCAP file was created
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    # Run mcap-tagg on the directory
    result = run_connector(
        "mcap",
        "mcap-tagg",
        ["--input-dir", str(output_dir)],
        timeout=30,
    )

    # Should complete successfully
    assert result.returncode == 0, f"mcap-tagg failed: {result.stderr}"
