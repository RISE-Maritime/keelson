#!/usr/bin/env python3

"""Tests for keelson2rtcm.py."""

import io
import time
from unittest.mock import Mock, patch

import pytest

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedBytes

from conftest import keelson2rtcm


def _make_mock_stdout():
    """Create a mock stdout with a writable buffer."""
    mock_buffer = io.BytesIO()
    mock_stdout = Mock()
    mock_stdout.buffer = mock_buffer
    return mock_stdout, mock_buffer


@pytest.mark.unit
class TestOnRtcmSample:
    """Tests for the Zenoh subscriber callback."""

    def test_writes_raw_bytes_to_stdout(self):
        """on_rtcm_sample should write extracted raw bytes to stdout.buffer."""
        raw_data = b"\xd3\x00\x0aHello RTCM"

        tb = TimestampedBytes()
        tb.timestamp.FromNanoseconds(time.time_ns())
        tb.value = raw_data
        envelope = keelson.enclose(tb.SerializeToString())

        sample = Mock()
        sample.payload.to_bytes.return_value = envelope

        mock_stdout, mock_buffer = _make_mock_stdout()
        with patch.object(keelson2rtcm.sys, "stdout", mock_stdout):
            keelson2rtcm.on_rtcm_sample(sample)

        assert mock_buffer.getvalue() == raw_data

    def test_handles_invalid_sample(self):
        """on_rtcm_sample should not raise on invalid data."""
        sample = Mock()
        sample.payload.to_bytes.return_value = b"not a valid envelope"

        mock_stdout, _ = _make_mock_stdout()
        with patch.object(keelson2rtcm.sys, "stdout", mock_stdout):
            keelson2rtcm.on_rtcm_sample(sample)

    def test_handles_empty_payload(self):
        """on_rtcm_sample should not raise on empty payload bytes."""
        sample = Mock()
        sample.payload.to_bytes.return_value = b""

        mock_stdout, _ = _make_mock_stdout()
        with patch.object(keelson2rtcm.sys, "stdout", mock_stdout):
            keelson2rtcm.on_rtcm_sample(sample)

    def test_handles_truncated_protobuf(self):
        """on_rtcm_sample should not raise on truncated protobuf envelope."""
        sample = Mock()
        sample.payload.to_bytes.return_value = b"\x0a\x04"

        mock_stdout, _ = _make_mock_stdout()
        with patch.object(keelson2rtcm.sys, "stdout", mock_stdout):
            keelson2rtcm.on_rtcm_sample(sample)

    def test_writes_multiple_samples(self):
        """Multiple calls should append bytes to stdout."""
        frames = [b"\xd3\x00\x01A", b"\xd3\x00\x01B"]

        mock_stdout, mock_buffer = _make_mock_stdout()
        with patch.object(keelson2rtcm.sys, "stdout", mock_stdout):
            for raw_data in frames:
                tb = TimestampedBytes()
                tb.timestamp.FromNanoseconds(time.time_ns())
                tb.value = raw_data
                envelope = keelson.enclose(tb.SerializeToString())

                sample = Mock()
                sample.payload.to_bytes.return_value = envelope
                keelson2rtcm.on_rtcm_sample(sample)

        assert mock_buffer.getvalue() == b"".join(frames)
