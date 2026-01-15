"""
End-to-end tests for the MCAP connector.

Tests actual recording, replay, and rotation functionality.
"""

import time
from pathlib import Path

import pytest
from mcap.reader import make_reader

# Import validation utilities from the local conftest
from conftest import validate_mcap_files


# =============================================================================
# mcap-record e2e tests
# =============================================================================


@pytest.mark.e2e
def test_mcap_record_creates_output_file(connector_process_factory, temp_dir: Path):
    """Test that mcap-record creates an MCAP output file."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        ["--key", "test/**", "--output-folder", str(output_dir), "--mode", "peer"],
    )
    recorder.start()
    time.sleep(2)
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    mcap_file = mcap_files[0]
    assert mcap_file.stat().st_size > 0, "MCAP file should not be empty"


@pytest.mark.e2e
def test_mcap_record_with_publisher(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record captures messages from a publisher."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "10",
            "--seconds_per_sweep", "0.5",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(3)

    publisher.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    mcap_file = mcap_files[0]
    file_size = mcap_file.stat().st_size
    assert file_size > 500, f"MCAP file should contain data, got {file_size} bytes"


@pytest.mark.e2e
def test_mcap_record_multiple_publishers(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record captures data from multiple publishers."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    radar1 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "vessel1",
            "--source-id", "radar1",
            "--spokes_per_sweep", "5",
            "--seconds_per_sweep", "0.3",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    radar2 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "vessel2",
            "--source-id", "radar2",
            "--spokes_per_sweep", "5",
            "--seconds_per_sweep", "0.3",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )

    radar1.start()
    radar2.start()
    time.sleep(2)

    radar1.stop()
    radar2.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        topics = [ch.topic for ch in summary.channels.values()]
        vessel1_topics = [t for t in topics if "vessel1" in t]
        vessel2_topics = [t for t in topics if "vessel2" in t]

        assert len(vessel1_topics) > 0, "Should have topics from vessel1"
        assert len(vessel2_topics) > 0, "Should have topics from vessel2"


# =============================================================================
# mcap-replay e2e tests
# =============================================================================


@pytest.mark.e2e
def test_mcap_replay_starts_successfully(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-replay can read and start replaying an MCAP file."""
    record_dir = temp_dir / "record"
    record_dir.mkdir()

    # Record some data
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(record_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "10",
            "--seconds_per_sweep", "0.5",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(2)

    publisher.stop()
    recorder.stop()

    mcap_files = list(record_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    original_mcap = mcap_files[0]

    with open(original_mcap, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        original_message_count = (
            sum(summary.statistics.channel_message_counts.values())
            if summary.statistics
            else 0
        )

    assert original_message_count > 0, "Original recording should have messages"

    replayer = connector_process_factory(
        "mcap",
        "mcap-replay",
        ["--mcap-file", str(original_mcap), "--mode", "peer"],
    )
    replayer.start()
    time.sleep(2)
    replayer.stop()


# =============================================================================
# mcap-tagg e2e tests
# =============================================================================


@pytest.mark.e2e
def test_mcap_tagg_with_empty_directory(run_connector, temp_dir: Path):
    """Test that mcap-tagg handles an empty directory gracefully."""
    result = run_connector("mcap", "mcap-tagg", ["--input-dir", str(temp_dir)])
    assert result.returncode == 0


@pytest.mark.e2e
def test_mcap_tagg_processes_files(
    connector_process_factory, run_connector, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-tagg processes MCAP files."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "10",
            "--seconds_per_sweep", "0.5",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(2)

    publisher.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    result = run_connector(
        "mcap", "mcap-tagg", ["--input-dir", str(output_dir)], timeout=30
    )
    assert result.returncode == 0, f"mcap-tagg failed: {result.stderr}"


# =============================================================================
# mcap-record rotation tests
# =============================================================================


@pytest.mark.e2e
def test_mcap_record_size_based_rotation(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record rotates files based on size threshold."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
            "--rotate-size", "5KB",
            "--file-name", "%Y-%m-%d_%H%M%S_%f",
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "50",
            "--seconds_per_sweep", "0.2",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(4)

    publisher.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) >= 2, f"Expected at least 2 MCAP files, found {len(mcap_files)}"

    valid_files, _ = validate_mcap_files(
        mcap_files, require_messages=True, allow_incomplete_last=True
    )
    assert len(valid_files) >= 2, f"Expected at least 2 valid files, got {len(valid_files)}"


@pytest.mark.e2e
def test_mcap_record_time_based_rotation(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record rotates files based on time interval."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
            "--rotate-when", "S",
            "--rotate-interval", "2",
            "--file-name", "%Y-%m-%d_%H%M%S_%f",
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "5",
            "--seconds_per_sweep", "0.5",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(5)

    publisher.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) >= 2, f"Expected at least 2 MCAP files, found {len(mcap_files)}"

    valid_files, _ = validate_mcap_files(
        mcap_files, require_messages=True, allow_incomplete_last=True
    )
    assert len(valid_files) >= 2, f"Expected at least 2 valid files, got {len(valid_files)}"


@pytest.mark.e2e
def test_mcap_record_sighup_rotation(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record rotates files on SIGHUP signal."""
    import os
    import signal

    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()
    pid_file = temp_dir / "mcap-record.pid"

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
            "--pid-file", str(pid_file),
            "--file-name", "%Y-%m-%d_%H%M%S_%f",
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "5",
            "--seconds_per_sweep", "0.5",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(1)

    assert pid_file.exists(), "PID file should be created"
    pid = int(pid_file.read_text().strip())

    os.kill(pid, signal.SIGHUP)
    time.sleep(1)
    os.kill(pid, signal.SIGHUP)
    time.sleep(1)

    publisher.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) >= 3, f"Expected at least 3 MCAP files, found {len(mcap_files)}"

    valid_files, _ = validate_mcap_files(
        mcap_files, require_messages=True, allow_incomplete_last=True
    )
    assert len(valid_files) >= 3, f"Expected at least 3 valid files, got {len(valid_files)}"


@pytest.mark.e2e
def test_mcap_record_rotation_preserves_channels(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that channel definitions are preserved across rotations."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
            "--rotate-size", "50KB",
            "--file-name", "%Y-%m-%d_%H%M%S_%f",
        ],
    )
    recorder.start()
    time.sleep(1)

    radar1 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "vessel1",
            "--source-id", "radar1",
            "--spokes_per_sweep", "50",
            "--seconds_per_sweep", "0.2",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    radar2 = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "vessel2",
            "--source-id", "radar2",
            "--spokes_per_sweep", "50",
            "--seconds_per_sweep", "0.2",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )

    radar1.start()
    radar2.start()
    time.sleep(8)

    radar1.stop()
    radar2.stop()
    recorder.stop()
    time.sleep(0.5)

    mcap_files = sorted(output_dir.glob("*.mcap"))
    assert len(mcap_files) >= 2, f"Expected at least 2 files, got {len(mcap_files)}"

    valid_files, _ = validate_mcap_files(
        mcap_files, require_messages=True, allow_incomplete_last=True
    )
    assert len(valid_files) >= 2, f"Expected at least 2 valid files, got {len(valid_files)}"

    file_topics = []
    for mcap_file, summary in valid_files:
        topics = set(ch.topic for ch in summary.channels.values())
        file_topics.append((mcap_file, topics))

    first_file_topics = file_topics[0][1]
    assert len(first_file_topics) > 0, "First file should have at least one channel"

    for mcap_file, topics in file_topics[1:]:
        missing = first_file_topics - topics
        assert len(missing) == 0, f"File {mcap_file.name} is missing channels: {missing}"

    all_topics = set()
    for _, topics in file_topics:
        all_topics.update(topics)

    vessel1_topics = [t for t in all_topics if "vessel1" in t]
    vessel2_topics = [t for t in all_topics if "vessel2" in t]
    assert len(vessel1_topics) > 0, "Should have vessel1 channels"
    assert len(vessel2_topics) > 0, "Should have vessel2 channels"


@pytest.mark.e2e
def test_mcap_record_no_rotation_by_default(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that mcap-record creates single file when no rotation is configured."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key", "test-realm/@v0/**",
            "--output-folder", str(output_dir),
            "--mode", "peer",
            "--listen", zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    publisher = connector_process_factory(
        "mockups",
        "mockup_radar",
        [
            "--realm", "test-realm",
            "--entity-id", "test-vessel",
            "--source-id", "radar1",
            "--spokes_per_sweep", "50",
            "--seconds_per_sweep", "0.2",
            "--mode", "peer",
            "--connect", zenoh_endpoints["connect"],
        ],
    )
    publisher.start()
    time.sleep(3)

    publisher.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected exactly 1 MCAP file, found {len(mcap_files)}"

    valid_files, invalid_files = validate_mcap_files(
        mcap_files, require_messages=True, allow_incomplete_last=False
    )
    assert len(valid_files) == 1, f"The single MCAP file should be valid: {invalid_files}"
