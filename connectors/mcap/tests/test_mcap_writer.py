"""Unit tests for MCAPRotatingWriter internals.

Fast, isolated tests (no Zenoh) for the core recording engine.
"""

import sys
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch, MagicMock

import pytest
from mcap.reader import make_reader
from mcap.well_known import SchemaEncoding, MessageEncoding

# Add the bin directory to the path so we can import the module
bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

from importlib import import_module

keelson2mcap = import_module("keelson2mcap")
MCAPRotatingWriter = keelson2mcap.MCAPRotatingWriter
SchemaDefinition = keelson2mcap.SchemaDefinition
ChannelDefinition = keelson2mcap.ChannelDefinition


# =============================================================================
# Lifecycle tests
# =============================================================================


class TestWriterLifecycle:
    """Tests for open/close/rotate lifecycle."""

    def test_open_creates_mcap_file(self, tmp_path):
        """Opening the writer should create an MCAP file on disk."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%Y%m%d_%H%M%S"
        )
        writer.open()
        try:
            assert writer._current_path is not None
            assert writer._current_path.exists()
            assert writer._current_path.suffix == ".mcap"
        finally:
            writer.close()

    def test_close_finalizes_file(self, tmp_path):
        """Closing the writer should produce a valid MCAP file."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%Y%m%d_%H%M%S"
        )
        writer.open()
        path = writer._current_path
        writer.close()

        # File should be readable by mcap reader
        with open(path, "rb") as f:
            reader = make_reader(f)
            summary = reader.get_summary()
            assert summary is not None

    def test_close_sets_writer_to_none(self, tmp_path):
        """After close, internal writer and file handle should be None."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%Y%m%d_%H%M%S"
        )
        writer.open()
        writer.close()

        assert writer._writer is None
        assert writer._file_handle is None

    def test_double_close_is_safe(self, tmp_path):
        """Calling close() twice should not raise."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%Y%m%d_%H%M%S"
        )
        writer.open()
        writer.close()
        # Second close should not raise
        writer.close()

    def test_close_without_open_is_safe(self, tmp_path):
        """Calling close() without open() should not raise."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%Y%m%d_%H%M%S"
        )
        # Should not raise
        writer.close()

    def test_rotate_creates_new_file(self, tmp_path):
        """Rotating should close old file and open a new one."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()
        first_path = writer._current_path

        # Small delay to get a different microsecond filename
        time.sleep(0.01)
        writer.rotate()
        second_path = writer._current_path

        assert first_path != second_path
        assert first_path.exists()
        assert second_path.exists()

        writer.close()

        # Both files should be valid MCAP
        for p in [first_path, second_path]:
            with open(p, "rb") as f:
                reader = make_reader(f)
                assert reader.get_summary() is not None


# =============================================================================
# Schema and channel registration tests
# =============================================================================


class TestSchemaAndChannelRegistration:
    """Tests for ensure_schema/ensure_channel idempotency and preservation."""

    def _make_writer(self, tmp_path):
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()
        return writer

    def test_ensure_schema_registers_new_schema(self, tmp_path):
        """First call to ensure_schema should register and return an ID."""
        writer = self._make_writer(tmp_path)
        try:
            schema_id = writer.ensure_schema(
                subject="test_subject",
                name="TestMessage",
                encoding=SchemaEncoding.Protobuf,
                data=b"fake-schema-data",
            )
            assert isinstance(schema_id, int)
            assert "test_subject" in writer.schema_defs
            assert "test_subject" in writer._schema_ids
        finally:
            writer.close()

    def test_ensure_schema_is_idempotent(self, tmp_path):
        """Calling ensure_schema twice with same subject returns same ID."""
        writer = self._make_writer(tmp_path)
        try:
            id1 = writer.ensure_schema(
                subject="test_subject",
                name="TestMessage",
                encoding=SchemaEncoding.Protobuf,
                data=b"fake-schema-data",
            )
            id2 = writer.ensure_schema(
                subject="test_subject",
                name="TestMessage",
                encoding=SchemaEncoding.Protobuf,
                data=b"fake-schema-data",
            )
            assert id1 == id2
        finally:
            writer.close()

    def test_ensure_channel_registers_new_channel(self, tmp_path):
        """First call to ensure_channel should register and return an ID."""
        writer = self._make_writer(tmp_path)
        try:
            writer.ensure_schema(
                subject="test_subject",
                name="TestMessage",
                encoding=SchemaEncoding.Protobuf,
                data=b"fake-schema-data",
            )
            channel_id = writer.ensure_channel(
                key="test/key",
                topic="test/key",
                message_encoding=MessageEncoding.Protobuf,
                schema_subject="test_subject",
            )
            assert isinstance(channel_id, int)
            assert "test/key" in writer.channel_defs
            assert "test/key" in writer._channel_ids
        finally:
            writer.close()

    def test_ensure_channel_is_idempotent(self, tmp_path):
        """Calling ensure_channel twice with same key returns same ID."""
        writer = self._make_writer(tmp_path)
        try:
            writer.ensure_schema(
                subject="test_subject",
                name="TestMessage",
                encoding=SchemaEncoding.Protobuf,
                data=b"fake-schema-data",
            )
            id1 = writer.ensure_channel(
                key="test/key",
                topic="test/key",
                message_encoding=MessageEncoding.Protobuf,
                schema_subject="test_subject",
            )
            id2 = writer.ensure_channel(
                key="test/key",
                topic="test/key",
                message_encoding=MessageEncoding.Protobuf,
                schema_subject="test_subject",
            )
            assert id1 == id2
        finally:
            writer.close()

    def test_schemas_preserved_across_rotation(self, tmp_path):
        """Schemas registered in file A should appear in file B after rotation."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()

        writer.ensure_schema(
            subject="test_subject",
            name="TestMessage",
            encoding=SchemaEncoding.Protobuf,
            data=b"fake-schema-data",
        )
        writer.ensure_channel(
            key="test/key",
            topic="test/key",
            message_encoding=MessageEncoding.Protobuf,
            schema_subject="test_subject",
        )

        time.sleep(0.01)
        writer.rotate()

        # After rotation, schema and channel should be re-registered with new IDs
        assert "test_subject" in writer._schema_ids
        assert "test/key" in writer._channel_ids
        # Definitions should still exist
        assert "test_subject" in writer.schema_defs
        assert "test/key" in writer.channel_defs

        writer.close()

    def test_channels_re_registered_with_new_ids_after_rotation(self, tmp_path):
        """After rotation, schema/channel IDs may differ (new MCAP writer)."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()

        writer.ensure_schema(
            subject="sub_a",
            name="MsgA",
            encoding=SchemaEncoding.Protobuf,
            data=b"schema-a",
        )
        writer.ensure_channel(
            key="key_a",
            topic="key_a",
            message_encoding=MessageEncoding.Protobuf,
            schema_subject="sub_a",
        )

        first_file = writer._current_path

        time.sleep(0.01)
        writer.rotate()

        second_file = writer._current_path

        # Write a message in the second file to ensure channel works
        channel_id = writer._channel_ids["key_a"]
        writer.write_message(channel_id, 1000, 2000, b"hello")

        writer.close()

        # Verify second file has the schema and message
        with open(second_file, "rb") as f:
            reader = make_reader(f)
            summary = reader.get_summary()
            assert len(summary.schemas) > 0
            assert len(summary.channels) > 0
            msgs = list(reader.iter_messages())
            assert len(msgs) == 1

    def test_multiple_schemas_preserved_across_rotation(self, tmp_path):
        """Multiple schemas should all be re-registered after rotation."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()

        for i in range(3):
            writer.ensure_schema(
                subject=f"subject_{i}",
                name=f"Message{i}",
                encoding=SchemaEncoding.Protobuf,
                data=f"schema-{i}".encode(),
            )
            writer.ensure_channel(
                key=f"key_{i}",
                topic=f"key_{i}",
                message_encoding=MessageEncoding.Protobuf,
                schema_subject=f"subject_{i}",
            )

        time.sleep(0.01)
        writer.rotate()

        # All 3 should be present
        for i in range(3):
            assert f"subject_{i}" in writer._schema_ids
            assert f"key_{i}" in writer._channel_ids

        writer.close()


# =============================================================================
# Rotation trigger tests
# =============================================================================


class TestRotationTriggers:
    """Tests for should_rotate logic."""

    def test_no_rotation_when_unconfigured(self, tmp_path):
        """Without any rotation config, should_rotate returns False."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        writer.open()
        try:
            assert writer.should_rotate() is False
        finally:
            writer.close()

    def test_size_based_rotation_triggers(self, tmp_path):
        """should_rotate returns True when _bytes_written >= max_size_bytes."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
            max_size_bytes=1000,
        )
        writer.open()
        try:
            assert writer.should_rotate() is False
            writer._bytes_written = 999
            assert writer.should_rotate() is False
            writer._bytes_written = 1000
            assert writer.should_rotate() is True
            writer._bytes_written = 2000
            assert writer.should_rotate() is True
        finally:
            writer.close()

    def test_time_based_rotation_triggers(self, tmp_path):
        """should_rotate returns True when current time >= rollover_at."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
            rotate_when="S",
            rotate_interval=1,
        )
        writer.open()
        try:
            # Rollover is set to now + 1 second, so shouldn't rotate yet
            assert writer.should_rotate() is False

            # Force rollover to past
            writer._rollover_at = time.time() - 1
            assert writer.should_rotate() is True
        finally:
            writer.close()

    def test_time_based_rotation_with_mocked_time(self, tmp_path):
        """Time-based rotation with mocked time.time."""
        fake_time = 1000000.0
        with patch("keelson2mcap.time") as mock_time:
            mock_time.time.return_value = fake_time
            mock_time.monotonic.return_value = 0.0
            mock_time.strftime = time.strftime
            mock_time.localtime = time.localtime

            writer = MCAPRotatingWriter(
                output_folder=tmp_path,
                file_pattern="test",
                rotate_when="S",
                rotate_interval=10,
            )
            # __post_init__ computes rollover using mocked time
            assert writer._rollover_at == pytest.approx(fake_time + 10, abs=1)

            writer.open()
            try:
                # Not yet time
                mock_time.time.return_value = fake_time + 5
                assert writer.should_rotate() is False

                # Now past rollover
                mock_time.time.return_value = fake_time + 11
                assert writer.should_rotate() is True
            finally:
                writer.close()

    def test_sighup_rotation_triggers(self, tmp_path):
        """should_rotate returns True when rotate_requested event is set."""
        event = Event()
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
            rotate_requested=event,
        )
        writer.open()
        try:
            assert writer.should_rotate() is False

            event.set()
            assert writer.should_rotate() is True

            # Event should be cleared after check
            assert not event.is_set()
            assert writer.should_rotate() is False
        finally:
            writer.close()

    def test_sighup_takes_priority_over_time_and_size(self, tmp_path):
        """SIGHUP check happens before time and size checks."""
        event = Event()
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
            max_size_bytes=100000,
            rotate_when="H",
            rotate_requested=event,
        )
        writer.open()
        try:
            event.set()
            # Even though size and time thresholds not met, SIGHUP triggers
            assert writer.should_rotate() is True
        finally:
            writer.close()

    def test_combined_size_and_time_rotation(self, tmp_path):
        """When both size and time are configured, either can trigger."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
            max_size_bytes=500,
            rotate_when="S",
            rotate_interval=1,
        )
        writer.open()
        try:
            assert writer.should_rotate() is False

            # Trigger by size
            writer._bytes_written = 600
            assert writer.should_rotate() is True

            # Reset size, trigger by time
            writer._bytes_written = 0
            writer._rollover_at = time.time() - 1
            assert writer.should_rotate() is True
        finally:
            writer.close()

    def test_rotation_resets_bytes_written(self, tmp_path):
        """After rotate(), _bytes_written should be reset to 0."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test_%f",
            max_size_bytes=500,
        )
        writer.open()

        writer._bytes_written = 600
        time.sleep(0.01)
        writer.rotate()

        assert writer._bytes_written == 0
        writer.close()

    def test_rotation_recomputes_rollover_time(self, tmp_path):
        """After rotate(), time-based rollover should be recomputed."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test_%f",
            rotate_when="H",
            rotate_interval=2,
        )
        writer.open()

        old_rollover = writer._rollover_at
        time.sleep(0.01)
        writer.rotate()

        # New rollover should be later than old one
        assert writer._rollover_at > old_rollover
        writer.close()


# =============================================================================
# Time interval calculation tests
# =============================================================================


class TestTimeIntervals:
    """Tests for _compute_next_rollover interval calculations."""

    @pytest.mark.parametrize(
        "when, interval, expected_seconds",
        [
            ("S", 1, 1),
            ("S", 5, 5),
            ("M", 1, 60),
            ("M", 10, 600),
            ("H", 1, 3600),
            ("H", 2, 7200),
            ("D", 1, 86400),
            ("midnight", 1, 86400),
            ("W0", 1, 604800),
        ],
    )
    def test_rollover_interval_calculation(self, tmp_path, when, interval, expected_seconds):
        """Verify rollover time is computed correctly for each 'when' setting."""
        before = time.time()
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
            rotate_when=when,
            rotate_interval=interval,
        )
        after = time.time()

        # Rollover should be between before+expected and after+expected
        assert writer._rollover_at >= before + expected_seconds - 0.1
        assert writer._rollover_at <= after + expected_seconds + 0.1

    def test_no_rollover_when_not_configured(self, tmp_path):
        """When rotate_when is None, _rollover_at should be None."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path,
            file_pattern="test",
        )
        assert writer._rollover_at is None


# =============================================================================
# Filename generation tests
# =============================================================================


class TestFilenameGeneration:
    """Tests for _generate_filename."""

    def test_strftime_pattern_expansion(self, tmp_path):
        """File pattern with strftime codes should produce expanded filename."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="%Y-%m-%d"
        )
        path = writer._generate_filename()
        # Should contain current year
        assert str(time.localtime().tm_year) in path.name
        assert path.suffix == ".mcap"

    def test_microsecond_uniqueness(self, tmp_path):
        """Using %f in pattern should produce unique filenames."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        names = set()
        for _ in range(10):
            p = writer._generate_filename()
            names.add(p.name)
            time.sleep(0.001)

        # Should have multiple unique names (timing dependent, but 10 attempts
        # with 1ms sleep should yield at least a few different microseconds)
        assert len(names) > 1

    def test_output_folder_is_respected(self, tmp_path):
        """Generated filename should be under the output_folder."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        path = writer._generate_filename()
        assert path.parent == tmp_path

    def test_suffix_is_always_mcap(self, tmp_path):
        """Even if pattern has no extension, .mcap suffix is added."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="recording_%H%M%S"
        )
        path = writer._generate_filename()
        assert path.suffix == ".mcap"

    def test_pattern_without_strftime(self, tmp_path):
        """A static pattern (no strftime codes) should still work."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="static_name"
        )
        path = writer._generate_filename()
        assert path.name == "static_name.mcap"


# =============================================================================
# Byte tracking accuracy tests
# =============================================================================


class TestByteTracking:
    """Tests for _bytes_written tracking."""

    def test_write_message_increments_bytes_written(self, tmp_path):
        """write_message should increment _bytes_written by data + 24 bytes overhead."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        writer.open()
        try:
            # Register a dummy schema and channel
            writer.ensure_schema(
                subject="s", name="M", encoding="proto", data=b"x"
            )
            writer.ensure_channel(
                key="k", topic="k", message_encoding="proto", schema_subject="s"
            )
            channel_id = writer._channel_ids["k"]

            assert writer._bytes_written == 0

            data = b"hello world"  # 11 bytes
            writer.write_message(channel_id, 100, 200, data)
            assert writer._bytes_written == 11 + 24

            data2 = b"x" * 100  # 100 bytes
            writer.write_message(channel_id, 300, 400, data2)
            assert writer._bytes_written == (11 + 24) + (100 + 24)
        finally:
            writer.close()

    def test_bytes_written_reset_on_open(self, tmp_path):
        """Opening a new file should reset _bytes_written to 0."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()
        writer.ensure_schema(
            subject="s", name="M", encoding="proto", data=b"x"
        )
        writer.ensure_channel(
            key="k", topic="k", message_encoding="proto", schema_subject="s"
        )
        writer.write_message(writer._channel_ids["k"], 100, 200, b"data")

        assert writer._bytes_written > 0

        time.sleep(0.01)
        writer.rotate()
        assert writer._bytes_written == 0
        writer.close()

    def test_empty_data_still_adds_overhead(self, tmp_path):
        """Even with empty data, 24-byte overhead should be added."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        writer.open()
        try:
            writer.ensure_schema(
                subject="s", name="M", encoding="proto", data=b"x"
            )
            writer.ensure_channel(
                key="k", topic="k", message_encoding="proto", schema_subject="s"
            )
            writer.write_message(writer._channel_ids["k"], 100, 200, b"")
            assert writer._bytes_written == 24
        finally:
            writer.close()


# =============================================================================
# Edge case tests
# =============================================================================


class TestEdgeCases:
    """Tests for unusual or edge-case scenarios."""

    def test_rotating_with_zero_messages(self, tmp_path):
        """Rotating a file with no messages should produce a valid MCAP."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()
        first_path = writer._current_path

        time.sleep(0.01)
        writer.rotate()
        writer.close()

        # First file should be valid even with zero messages
        with open(first_path, "rb") as f:
            reader = make_reader(f)
            summary = reader.get_summary()
            assert summary is not None

    def test_schema_data_preserved_exactly(self, tmp_path):
        """Schema data stored in schema_defs should be byte-identical."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        writer.open()
        try:
            original_data = b"\x00\x01\x02\xff" * 100
            writer.ensure_schema(
                subject="s",
                name="M",
                encoding=SchemaEncoding.Protobuf,
                data=original_data,
            )
            assert writer.schema_defs["s"].data == original_data
        finally:
            writer.close()

    def test_many_rotations_in_sequence(self, tmp_path):
        """Performing many rotations in sequence should not fail."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()

        writer.ensure_schema(
            subject="s", name="M", encoding="proto", data=b"x"
        )
        writer.ensure_channel(
            key="k", topic="k", message_encoding="proto", schema_subject="s"
        )

        paths = [writer._current_path]
        for i in range(10):
            time.sleep(0.005)
            writer.rotate()
            paths.append(writer._current_path)
            # Write a message in each file to prove it works
            writer.write_message(writer._channel_ids["k"], i * 100, i * 100, b"msg")

        writer.close()

        # All files should exist and be valid
        for p in paths:
            assert p.exists()
            with open(p, "rb") as f:
                reader = make_reader(f)
                assert reader.get_summary() is not None

    def test_large_message_write(self, tmp_path):
        """Writing a large message should work and track bytes correctly."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        writer.open()
        try:
            writer.ensure_schema(
                subject="s", name="M", encoding="proto", data=b"x"
            )
            writer.ensure_channel(
                key="k", topic="k", message_encoding="proto", schema_subject="s"
            )
            big_data = b"\x42" * (1024 * 1024)  # 1 MB
            writer.write_message(writer._channel_ids["k"], 100, 200, big_data)

            assert writer._bytes_written == len(big_data) + 24
        finally:
            writer.close()

    def test_written_messages_are_readable(self, tmp_path):
        """Messages written via write_message should be readable from the MCAP file."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test"
        )
        writer.open()

        writer.ensure_schema(
            subject="s", name="M", encoding="proto", data=b"schema-bytes"
        )
        writer.ensure_channel(
            key="k", topic="my/topic", message_encoding="proto", schema_subject="s"
        )
        channel_id = writer._channel_ids["k"]

        payloads = [b"msg_0", b"msg_1", b"msg_2"]
        for i, p in enumerate(payloads):
            writer.write_message(channel_id, i * 1000, i * 1000 + 500, p)

        path = writer._current_path
        writer.close()

        # Read back and verify
        with open(path, "rb") as f:
            reader = make_reader(f)
            messages = list(reader.iter_messages())
            assert len(messages) == 3

            for i, (schema, channel, message) in enumerate(messages):
                assert message.data == payloads[i]
                assert message.log_time == i * 1000
                assert message.publish_time == i * 1000 + 500
                assert channel.topic == "my/topic"

    def test_messages_span_across_rotation(self, tmp_path):
        """Messages written before and after rotation should be in their respective files."""
        writer = MCAPRotatingWriter(
            output_folder=tmp_path, file_pattern="test_%f"
        )
        writer.open()

        writer.ensure_schema(
            subject="s", name="M", encoding="proto", data=b"x"
        )
        writer.ensure_channel(
            key="k", topic="k", message_encoding="proto", schema_subject="s"
        )

        # Write 2 messages to first file
        writer.write_message(writer._channel_ids["k"], 100, 100, b"file1_msg1")
        writer.write_message(writer._channel_ids["k"], 200, 200, b"file1_msg2")
        first_path = writer._current_path

        time.sleep(0.01)
        writer.rotate()

        # Write 3 messages to second file
        writer.write_message(writer._channel_ids["k"], 300, 300, b"file2_msg1")
        writer.write_message(writer._channel_ids["k"], 400, 400, b"file2_msg2")
        writer.write_message(writer._channel_ids["k"], 500, 500, b"file2_msg3")
        second_path = writer._current_path

        writer.close()

        with open(first_path, "rb") as f:
            msgs = list(make_reader(f).iter_messages())
            assert len(msgs) == 2

        with open(second_path, "rb") as f:
            msgs = list(make_reader(f).iter_messages())
            assert len(msgs) == 3
