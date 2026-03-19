#!/usr/bin/env python3

"""Tests for rtcm2keelson.py."""

import io
import time

import pytest

import keelson
from keelson.helpers import enclose_from_bytes
from keelson.payloads.Primitives_pb2 import TimestampedBytes
from pyrtcm import RTCMReader

from conftest import RTCM_1005_FRAME


@pytest.mark.unit
class TestRoundTrip:
    """Test that RTCM frame bytes survive the enclose/uncover round trip."""

    def test_bytes_round_trip(self):
        """Raw bytes should survive enclose -> uncover -> TimestampedBytes."""
        raw_frame = b"\xd3\x00\x13" + b"\x00" * 19 + b"\x00\x00\x00"
        now = time.time_ns()

        envelope = enclose_from_bytes(raw_frame, now)
        _received_at, enclosed_at, payload_bytes = keelson.uncover(envelope)

        tb = TimestampedBytes.FromString(payload_bytes)
        assert tb.value == raw_frame
        assert tb.timestamp.ToNanoseconds() == now

    def test_empty_bytes(self):
        """Empty bytes should round-trip correctly."""
        envelope = enclose_from_bytes(b"", time.time_ns())
        _received_at, _enclosed_at, payload_bytes = keelson.uncover(envelope)

        tb = TimestampedBytes.FromString(payload_bytes)
        assert tb.value == b""


@pytest.mark.unit
class TestRTCMReaderEmptyStream:
    """Test RTCMReader behavior with an empty stream."""

    def test_empty_stream_yields_nothing(self):
        """RTCMReader on an empty stream should produce no frames."""
        stream = io.BytesIO(b"")
        reader = RTCMReader(stream)
        frames = list(reader)
        assert frames == []


@pytest.mark.unit
class TestStdinReading:
    """Test that RTCMReader can parse RTCM frames from a BytesIO stream (stdin proxy)."""

    def test_reads_valid_frame_from_stream(self):
        """RTCMReader should parse a valid RTCM 1005 frame from a stream."""
        stream = io.BytesIO(RTCM_1005_FRAME)
        reader = RTCMReader(stream)
        frames = list(reader)
        assert len(frames) == 1
        raw_data, parsed_data = frames[0]
        assert raw_data is not None
        assert len(raw_data) == len(RTCM_1005_FRAME)

    def test_reads_multiple_frames(self):
        """RTCMReader should parse multiple concatenated frames."""
        stream = io.BytesIO(RTCM_1005_FRAME * 3)
        reader = RTCMReader(stream)
        frames = list(reader)
        assert len(frames) == 3


@pytest.mark.unit
class TestPyrtcmExceptionImports:
    """Verify that the pyrtcm exception types used in rtcm2keelson are importable."""

    def test_parse_error_importable(self):
        from pyrtcm import RTCMParseError

        assert issubclass(RTCMParseError, Exception)

    def test_message_error_importable(self):
        from pyrtcm import RTCMMessageError

        assert issubclass(RTCMMessageError, Exception)

    def test_type_error_importable(self):
        from pyrtcm import RTCMTypeError

        assert issubclass(RTCMTypeError, Exception)
