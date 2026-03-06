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


@pytest.mark.unit
class TestRTCMReaderEmptyStream:
    """Test RTCMReader behavior with an empty stream."""

    def test_empty_stream_yields_nothing(self):
        """RTCMReader on an empty stream should produce no frames."""
        stream = io.BytesIO(b"")
        reader = RTCMReader(stream)
        frames = list(reader)
        assert frames == []
