#!/usr/bin/env python3

"""Tests for rtcm2keelson.py."""

import io
import time

import pytest

import keelson
from keelson.helpers import enclose_from_bytes
from keelson.payloads.Primitives_pb2 import TimestampedBytes
from pyrtcm import RTCMReader

from conftest import rtcm2keelson


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
class TestBackoffConstants:
    """Test that reconnection backoff constants exist and are valid."""

    def test_initial_backoff_is_positive(self):
        assert rtcm2keelson.INITIAL_BACKOFF > 0

    def test_max_backoff_greater_than_initial(self):
        assert rtcm2keelson.MAX_BACKOFF > rtcm2keelson.INITIAL_BACKOFF

    def test_max_backoff_is_reasonable(self):
        assert rtcm2keelson.MAX_BACKOFF <= 120

    def test_backoff_doubling(self):
        """Backoff should double each iteration, capped at MAX_BACKOFF."""
        backoff = rtcm2keelson.INITIAL_BACKOFF
        seen = [backoff]
        for _ in range(10):
            backoff = min(backoff * 2, rtcm2keelson.MAX_BACKOFF)
            seen.append(backoff)

        # Should double: 1, 2, 4, 8, 16, 32, 60, 60, 60, 60, 60
        assert seen[0] == 1.0
        assert seen[1] == 2.0
        assert seen[2] == 4.0
        assert all(b <= rtcm2keelson.MAX_BACKOFF for b in seen)
        assert seen[-1] == rtcm2keelson.MAX_BACKOFF


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
