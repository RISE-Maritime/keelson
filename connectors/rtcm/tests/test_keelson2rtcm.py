#!/usr/bin/env python3

"""Tests for keelson2rtcm.py."""

import asyncio
import time
from unittest.mock import Mock

import pytest

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedBytes

from conftest import keelson2rtcm


@pytest.mark.unit
class TestRTCMDistributor:
    """Tests for the RTCMDistributor class."""

    def test_add_client_returns_queue(self, distributor):
        queue = distributor.add_client()
        assert isinstance(queue, asyncio.Queue)
        assert distributor.client_count == 1

    def test_remove_client(self, distributor):
        queue = distributor.add_client()
        distributor.remove_client(queue)
        assert distributor.client_count == 0

    def test_remove_nonexistent_client(self, distributor):
        queue = asyncio.Queue()
        distributor.remove_client(queue)  # Should not raise
        assert distributor.client_count == 0

    def test_distribute_to_multiple_clients(self, distributor):
        q1 = distributor.add_client()
        q2 = distributor.add_client()
        q3 = distributor.add_client()

        data = b"\xd3\x00\x05test_data"
        distributor.distribute(data)

        assert q1.get_nowait() == data
        assert q2.get_nowait() == data
        assert q3.get_nowait() == data

    def test_distribute_drops_on_full_queue(self, distributor):
        queue = distributor.add_client()

        # Fill the queue (maxsize=100)
        for i in range(100):
            distributor.distribute(bytes([i]))

        # This should not raise — it drops the frame
        distributor.distribute(b"overflow")

        # Queue should still have exactly 100 items
        assert queue.qsize() == 100

    def test_distribute_no_clients(self, distributor):
        # Should not raise when no clients connected
        distributor.distribute(b"test")


@pytest.mark.unit
class TestBuildSourcetable:
    """Tests for NTRIP sourcetable generation."""

    def test_contains_str_record(self):
        result = keelson2rtcm.build_sourcetable("RTCM3")
        assert result.startswith("STR;RTCM3;")

    def test_contains_endsourcetable(self):
        result = keelson2rtcm.build_sourcetable("RTCM3")
        assert "ENDSOURCETABLE" in result

    def test_custom_mountpoint(self):
        result = keelson2rtcm.build_sourcetable("MY_MOUNT")
        assert "STR;MY_MOUNT;" in result

    def test_ends_with_crlf(self):
        result = keelson2rtcm.build_sourcetable("RTCM3")
        assert result.endswith("\r\n")


@pytest.mark.unit
class TestOnRtcmSample:
    """Tests for the Zenoh subscriber callback."""

    def test_extracts_raw_bytes(self, distributor):
        """on_rtcm_sample should correctly extract raw bytes from envelope."""
        raw_data = b"\xd3\x00\x0aHello RTCM"

        # Build a keelson envelope with TimestampedBytes
        tb = TimestampedBytes()
        tb.timestamp.FromNanoseconds(time.time_ns())
        tb.value = raw_data
        envelope = keelson.enclose(tb.SerializeToString())

        # Create a mock sample
        sample = Mock()
        sample.payload.to_bytes.return_value = envelope

        queue = distributor.add_client()
        keelson2rtcm.on_rtcm_sample(sample, distributor)

        assert queue.get_nowait() == raw_data

    def test_handles_invalid_sample(self, distributor):
        """on_rtcm_sample should not raise on invalid data."""
        sample = Mock()
        sample.payload.to_bytes.return_value = b"not a valid envelope"

        # Should not raise
        keelson2rtcm.on_rtcm_sample(sample, distributor)


@pytest.mark.unit
class TestNTRIPProtocol:
    """Tests for NTRIP protocol responses using a real asyncio TCP server."""

    def _ntrip_exchange(self, request_bytes, distributor, mountpoint="RTCM3"):
        """Start NTRIP server, send request, return response bytes."""

        async def _inner():
            async def handler(r, w):
                await keelson2rtcm.handle_ntrip_client(r, w, distributor, mountpoint)

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request_bytes)
            await writer.drain()

            # Read until server closes connection
            response = await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()
            return response

        return asyncio.run(_inner())

    def test_sourcetable_request(self, distributor):
        """GET / should return a sourcetable."""
        response = self._ntrip_exchange(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n", distributor
        )
        assert b"SOURCETABLE 200 OK" in response
        assert b"ENDSOURCETABLE" in response

    def test_unknown_mountpoint_returns_404(self, distributor):
        """GET /<wrong> should return ICY 404."""
        response = self._ntrip_exchange(
            b"GET /WRONG HTTP/1.1\r\nHost: localhost\r\n\r\n", distributor
        )
        assert b"ICY 404 Not Found" in response
