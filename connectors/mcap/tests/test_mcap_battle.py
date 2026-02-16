"""
End-to-end battle tests for the MCAP connector.

These tests push the recorder into uncomfortable territory:
data integrity verification, invalid inputs, rapid signals,
edge-case shutdown scenarios, and round-trip replays.
"""

import os
import signal
import socket
import time
from pathlib import Path

import pytest
from mcap.reader import make_reader

import keelson

# Import validation utilities from local module
from mcap_test_utils import validate_mcap_files


def _get_free_port() -> int:
    """Get a free port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# =============================================================================
# Helpers
# =============================================================================


def _read_mcap_messages(mcap_path: Path):
    """Read all messages from an MCAP file, returns list of (schema, channel, message)."""
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        return list(reader.iter_messages())


def _read_mcap_summary(mcap_path: Path):
    """Read summary from an MCAP file."""
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        return reader.get_summary()


def _total_message_count(mcap_path: Path) -> int:
    """Count total messages in an MCAP file."""
    summary = _read_mcap_summary(mcap_path)
    if summary and summary.statistics:
        return summary.statistics.message_count
    return len(_read_mcap_messages(mcap_path))


# =============================================================================
# Data integrity round-trip tests
# =============================================================================


@pytest.mark.e2e
class TestDataIntegrity:
    """Verify payload bytes, timestamps, and schemas survive recording."""

    def test_recorded_messages_have_valid_protobuf_schema(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Recorded MCAP files should contain decodable protobuf schemas."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

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
                "5",
                "--seconds_per_sweep",
                "0.5",
                "--mode",
                "peer",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        publisher.start()
        time.sleep(3)

        publisher.stop()
        recorder.stop()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        with open(mcap_files[0], "rb") as f:
            reader = make_reader(f)
            summary = reader.get_summary()
            assert summary is not None

            # Verify schemas exist and have protobuf encoding
            assert len(summary.schemas) > 0, "Should have at least one schema"
            for schema in summary.schemas.values():
                assert schema.encoding == "protobuf", (
                    f"Schema {schema.name} has encoding {schema.encoding}"
                )
                assert len(schema.data) > 0, (
                    f"Schema {schema.name} should have non-empty data"
                )

            # Verify channels reference valid schemas
            for channel in summary.channels.values():
                assert channel.schema_id in summary.schemas, (
                    f"Channel {channel.topic} references unknown schema {channel.schema_id}"
                )

            # Verify messages exist and have payload data
            messages = list(reader.iter_messages())
            assert len(messages) > 0, "Should have recorded some messages"

            for schema, channel, message in messages:
                assert len(message.data) > 0, "Message should have payload"
                assert message.log_time > 0, "log_time should be set"
                assert message.publish_time > 0, "publish_time should be set"

    def test_recorded_timestamps_are_sane(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Recorded log_time and publish_time should be recent nanosecond timestamps."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

        before_ns = time.time_ns()

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
                "5",
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

        after_ns = time.time_ns()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        messages = _read_mcap_messages(mcap_files[0])
        assert len(messages) > 0

        for _, _, message in messages:
            # log_time = received_at from keelson.uncover (recorder side)
            assert message.log_time >= before_ns, (
                f"log_time {message.log_time} is before test start {before_ns}"
            )
            assert message.log_time <= after_ns, (
                f"log_time {message.log_time} is after test end {after_ns}"
            )
            # publish_time = enclosed_at from envelope (publisher side)
            assert message.publish_time >= before_ns, (
                f"publish_time {message.publish_time} is before test start"
            )
            assert message.publish_time <= after_ns, (
                f"publish_time {message.publish_time} is after test end"
            )


# =============================================================================
# Invalid envelope handling tests
# =============================================================================


@pytest.mark.e2e
class TestInvalidEnvelopeHandling:
    """Test recorder resilience to malformed messages."""

    def test_raw_bytes_do_not_crash_recorder(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Publishing raw (non-envelope) bytes should not crash the recorder."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

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

        # Publish raw bytes directly via a simple zenoh publisher
        import zenoh

        conf = zenoh.Config()
        conf.insert_json5("mode", '"peer"')
        conf.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
        session = zenoh.open(conf)

        key = "test-realm/@v0/entity1/pubsub/radar_spoke/source1"
        pub = session.declare_publisher(key)

        # Send invalid (non-envelope) data
        for _ in range(5):
            pub.put(b"this is not a valid envelope")
            time.sleep(0.1)

        # Now send valid enveloped data
        for _ in range(3):
            envelope = keelson.enclose(payload=b"valid-payload")
            pub.put(envelope)
            time.sleep(0.1)

        time.sleep(1)
        pub.undeclare()
        session.close()

        # Recorder should still be running
        assert recorder.is_running(), "Recorder should not have crashed"
        recorder.stop()

        # MCAP file should exist and be valid
        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        valid_files, _ = validate_mcap_files(mcap_files)
        assert len(valid_files) == 1

        # The valid messages should have been recorded
        messages = _read_mcap_messages(mcap_files[0])
        assert len(messages) >= 3, (
            f"Expected at least 3 valid messages, got {len(messages)}"
        )


# =============================================================================
# Multiple key expression tests
# =============================================================================


@pytest.mark.e2e
class TestMultipleKeyExpressions:
    """Test recording from multiple distinct key patterns."""

    def test_multiple_key_args_all_recorded(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Subscribing to multiple -k patterns should capture from all of them."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "realm-a/@v0/**",
                "--key",
                "realm-b/@v0/**",
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

        import zenoh

        conf = zenoh.Config()
        conf.insert_json5("mode", '"peer"')
        conf.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
        session = zenoh.open(conf)

        key_a = "realm-a/@v0/entity1/pubsub/radar_spoke/source1"
        key_b = "realm-b/@v0/entity2/pubsub/radar_spoke/source2"

        pub_a = session.declare_publisher(key_a)
        pub_b = session.declare_publisher(key_b)

        for _ in range(5):
            pub_a.put(keelson.enclose(payload=b"from-realm-a"))
            pub_b.put(keelson.enclose(payload=b"from-realm-b"))
            time.sleep(0.1)

        time.sleep(1)
        pub_a.undeclare()
        pub_b.undeclare()
        session.close()
        recorder.stop()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        summary = _read_mcap_summary(mcap_files[0])
        topics = {ch.topic for ch in summary.channels.values()}

        assert key_a in topics, f"Expected {key_a} in recorded topics: {topics}"
        assert key_b in topics, f"Expected {key_b} in recorded topics: {topics}"


# =============================================================================
# Graceful shutdown / queue drain tests
# =============================================================================


@pytest.mark.e2e
class TestGracefulShutdown:
    """Verify MCAP files are properly finalized on shutdown."""

    def test_mcap_valid_after_quick_shutdown(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Publishing many messages then immediately stopping should produce a valid MCAP."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

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

        import zenoh

        conf = zenoh.Config()
        conf.insert_json5("mode", '"peer"')
        conf.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
        session = zenoh.open(conf)

        key = "test-realm/@v0/entity1/pubsub/radar_spoke/source1"
        pub = session.declare_publisher(key)

        # Burst a lot of messages
        for i in range(50):
            pub.put(keelson.enclose(payload=f"burst-msg-{i}".encode()))

        # Immediately stop
        pub.undeclare()
        session.close()
        recorder.stop()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        # File should be valid (not corrupted/truncated)
        valid_files, invalid_files = validate_mcap_files(mcap_files)
        assert len(valid_files) == 1, (
            f"MCAP should be valid after graceful shutdown, invalid: {invalid_files}"
        )

        summary = valid_files[0][1]
        assert summary.statistics is not None, "Should have summary statistics"


# =============================================================================
# Output folder creation tests
# =============================================================================


@pytest.mark.e2e
class TestOutputFolder:
    """Test output folder handling edge cases."""

    def test_nonexistent_output_folder_creates_no_mcap(
        self, connector_process_factory, temp_dir: Path
    ):
        """Recorder with nonexistent output folder should not create any MCAP files.

        The recorder thread crashes on open(), but the main process may stay alive
        since the recorder thread is a daemon thread. We verify no files are created.
        """
        nonexistent = temp_dir / "does_not_exist"

        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test/**",
                "--output-folder",
                str(nonexistent),
                "--mode",
                "peer",
            ],
        )
        recorder.start()
        time.sleep(2)
        recorder.stop()

        # No MCAP files should have been created
        assert not nonexistent.exists(), (
            "Nonexistent output folder should not be auto-created"
        )
        # No files anywhere in temp_dir matching *.mcap
        mcap_files = list(temp_dir.rglob("*.mcap"))
        assert len(mcap_files) == 0, (
            f"No MCAP files should exist, found: {mcap_files}"
        )


# =============================================================================
# Empty recording tests
# =============================================================================


@pytest.mark.e2e
class TestEmptyRecording:
    """Test recording with no publishers."""

    def test_empty_recording_produces_valid_mcap(
        self, connector_process_factory, temp_dir: Path
    ):
        """Starting recorder with no publishers, then stopping, should produce a valid MCAP."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test/**",
                "--output-folder",
                str(output_dir),
                "--mode",
                "peer",
            ],
        )
        recorder.start()
        time.sleep(2)
        recorder.stop()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1, "Should create exactly one MCAP file"

        # File should be valid
        with open(mcap_files[0], "rb") as f:
            reader = make_reader(f)
            summary = reader.get_summary()
            assert summary is not None, "Empty MCAP file should have valid summary"

        # Should have zero messages
        messages = _read_mcap_messages(mcap_files[0])
        assert len(messages) == 0, "Empty recording should have zero messages"


# =============================================================================
# Rapid SIGHUP signal tests
# =============================================================================


@pytest.mark.e2e
class TestRapidSIGHUP:
    """Test recorder resilience to rapid SIGHUP signals."""

    def test_rapid_sighups_no_crash(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Sending many SIGHUPs in quick succession should not crash the recorder."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()
        pid_file = temp_dir / "recorder.pid"

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
                "--pid-file",
                str(pid_file),
                "--file-name",
                "%Y-%m-%d_%H%M%S_%f",
            ],
        )
        recorder.start()
        time.sleep(1)

        # Start a publisher to have data flowing
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
                "5",
                "--seconds_per_sweep",
                "0.5",
                "--mode",
                "peer",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        publisher.start()
        time.sleep(1)

        assert pid_file.exists(), "PID file should be created"
        pid = int(pid_file.read_text().strip())

        # Send 5 SIGHUPs in rapid succession
        for _ in range(5):
            os.kill(pid, signal.SIGHUP)
            time.sleep(0.1)

        # Let things settle
        time.sleep(2)

        # Recorder should still be running
        assert recorder.is_running(), "Recorder should survive rapid SIGHUPs"

        publisher.stop()
        recorder.stop()

        # Should have created multiple files
        mcap_files = sorted(output_dir.glob("*.mcap"))
        assert len(mcap_files) >= 2, (
            f"Expected at least 2 files after SIGHUPs, got {len(mcap_files)}"
        )

        # All files should be valid MCAP (allow last to be incomplete)
        valid_files, _ = validate_mcap_files(
            mcap_files, require_messages=False, allow_incomplete_last=True
        )
        assert len(valid_files) >= 2, (
            f"Expected at least 2 valid files, got {len(valid_files)}"
        )


# =============================================================================
# Data read-back integrity tests
# =============================================================================


@pytest.mark.e2e
class TestRecordedDataReadBack:
    """Verify recorded data can be read back with full fidelity."""

    def test_record_and_readback_payload_integrity(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Record known payloads, read MCAP back, verify payload bytes match."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

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

        # Publish known payloads via Zenoh directly
        import zenoh

        conf = zenoh.Config()
        conf.insert_json5("mode", '"peer"')
        conf.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
        session = zenoh.open(conf)

        key = "test-realm/@v0/entity1/pubsub/radar_spoke/source1"
        pub = session.declare_publisher(key)

        # Send known payloads
        sent_payloads = []
        for i in range(10):
            payload = f"test-payload-{i:04d}".encode()
            sent_payloads.append(payload)
            envelope = keelson.enclose(payload=payload)
            pub.put(envelope)
            time.sleep(0.05)

        time.sleep(1)
        pub.undeclare()
        session.close()
        recorder.stop()

        # Read back and verify payload integrity
        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        messages = _read_mcap_messages(mcap_files[0])
        assert len(messages) == len(sent_payloads), (
            f"Expected {len(sent_payloads)} messages, got {len(messages)}"
        )

        recorded_payloads = [msg.data for _, _, msg in messages]
        for sent in sent_payloads:
            assert sent in recorded_payloads, (
                f"Payload {sent!r} not found in recorded data"
            )

    def test_recorded_channels_match_publish_keys(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Channel topics in the MCAP should match the Zenoh keys published on."""
        output_dir = temp_dir / "mcap_output"
        output_dir.mkdir()

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

        import zenoh

        conf = zenoh.Config()
        conf.insert_json5("mode", '"peer"')
        conf.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
        session = zenoh.open(conf)

        expected_keys = [
            "test-realm/@v0/entity1/pubsub/radar_spoke/src1",
            "test-realm/@v0/entity1/pubsub/radar_spoke/src2",
        ]
        publishers = [session.declare_publisher(k) for k in expected_keys]

        for pub in publishers:
            for _ in range(3):
                pub.put(keelson.enclose(payload=b"data"))
                time.sleep(0.05)

        time.sleep(1)
        for pub in publishers:
            pub.undeclare()
        session.close()
        recorder.stop()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert len(mcap_files) == 1

        summary = _read_mcap_summary(mcap_files[0])
        recorded_topics = {ch.topic for ch in summary.channels.values()}

        for key in expected_keys:
            assert key in recorded_topics, (
                f"Expected key {key} in recorded topics {recorded_topics}"
            )


# =============================================================================
# Replay with --replay-key-tag tests
# =============================================================================


@pytest.mark.e2e
class TestReplayKeyTag:
    """Test that --replay-key-tag appends /replay to topics."""

    def test_replay_key_tag_flag_accepted(self, run_connector):
        """Verify --replay-key-tag is a valid CLI flag for mcap-replay."""
        result = run_connector("mcap", "mcap-replay", ["--help"])
        assert result.returncode == 0
        assert "--replay-key-tag" in result.stdout, (
            "mcap-replay should accept --replay-key-tag flag"
        )

    def test_replay_key_tag_appends_suffix(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Replay with --replay-key-tag should publish on topic/replay."""
        record_dir = temp_dir / "record"
        record_dir.mkdir()
        replay_dir = temp_dir / "replay"
        replay_dir.mkdir()

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
                "5",
                "--seconds_per_sweep",
                "0.3",
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

        mcap_files = list(record_dir.glob("*.mcap"))
        assert len(mcap_files) == 1
        original_mcap = mcap_files[0]

        original_summary = _read_mcap_summary(original_mcap)
        original_topics = {ch.topic for ch in original_summary.channels.values()}
        assert len(original_topics) > 0, "Should have recorded topics"

        # Step 2: Replay with --replay-key-tag and re-record
        replay_port = _get_free_port()
        replay_endpoint = f"tcp/127.0.0.1:{replay_port}"

        replay_recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test-realm/@v0/**",
                "--output-folder",
                str(replay_dir),
                "--mode",
                "peer",
                "--listen",
                replay_endpoint,
            ],
        )
        replay_recorder.start()
        time.sleep(3)

        replayer = connector_process_factory(
            "mcap",
            "mcap-replay",
            [
                "--mcap-file",
                str(original_mcap),
                "--replay-key-tag",
                "--mode",
                "peer",
                "--connect",
                replay_endpoint,
            ],
        )
        replayer.start()
        replayer.wait(timeout=30)
        time.sleep(2)
        replay_recorder.stop()

        replay_files = list(replay_dir.glob("*.mcap"))
        assert len(replay_files) == 1

        replay_summary = _read_mcap_summary(replay_files[0])
        replay_topics = {ch.topic for ch in replay_summary.channels.values()}

        assert len(replay_topics) > 0, "Replay recording should have captured topics"

        for original_topic in original_topics:
            expected = original_topic + "/replay"
            assert expected in replay_topics, (
                f"Expected replayed topic {expected} in {replay_topics}"
            )


# =============================================================================
# Record-replay round-trip tests
# =============================================================================


@pytest.mark.e2e
class TestRecordReplayRoundTrip:
    """Test the full record -> replay -> re-record cycle."""

    def test_replay_preserves_message_count(
        self, connector_process_factory, temp_dir: Path, zenoh_endpoints
    ):
        """Replayed recording should have the same number of messages."""
        record_dir = temp_dir / "record"
        record_dir.mkdir()
        replay_dir = temp_dir / "replay"
        replay_dir.mkdir()

        # Step 1: Record
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
        time.sleep(3)

        publisher.stop()
        recorder.stop()

        mcap_files = list(record_dir.glob("*.mcap"))
        assert len(mcap_files) == 1
        original_mcap = mcap_files[0]
        original_count = _total_message_count(original_mcap)
        assert original_count > 0, "Original recording should have messages"

        # Step 2: Replay and re-record
        replay_port = _get_free_port()
        replay_endpoint = f"tcp/127.0.0.1:{replay_port}"

        re_recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test-realm/@v0/**",
                "--output-folder",
                str(replay_dir),
                "--mode",
                "peer",
                "--listen",
                replay_endpoint,
            ],
        )
        re_recorder.start()
        time.sleep(3)

        replayer = connector_process_factory(
            "mcap",
            "mcap-replay",
            [
                "--mcap-file",
                str(original_mcap),
                "--mode",
                "peer",
                "--connect",
                replay_endpoint,
            ],
        )
        replayer.start()
        replayer.wait(timeout=30)
        time.sleep(2)
        re_recorder.stop()

        replay_files = list(replay_dir.glob("*.mcap"))
        assert len(replay_files) == 1
        replay_count = _total_message_count(replay_files[0])

        assert replay_count == original_count, (
            f"Replay message count ({replay_count}) should match "
            f"original ({original_count})"
        )
