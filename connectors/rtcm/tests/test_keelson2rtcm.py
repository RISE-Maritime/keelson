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

    def test_handles_empty_payload(self, distributor):
        """on_rtcm_sample should not raise on empty payload bytes."""
        sample = Mock()
        sample.payload.to_bytes.return_value = b""

        keelson2rtcm.on_rtcm_sample(sample, distributor)
        assert distributor.client_count == 0  # no crash

    def test_handles_truncated_protobuf(self, distributor):
        """on_rtcm_sample should not raise on truncated protobuf envelope."""
        # Valid envelope start bytes but truncated
        sample = Mock()
        sample.payload.to_bytes.return_value = b"\x0a\x04"

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

    def test_too_many_headers_returns_400(self, distributor):
        """Sending more than _MAX_NTRIP_HEADERS should return 400."""
        # Build a request with 40 headers (exceeds _MAX_NTRIP_HEADERS=32)
        headers = b"GET /RTCM3 HTTP/1.1\r\n"
        for i in range(40):
            headers += f"X-Header-{i}: value\r\n".encode()
        headers += b"\r\n"

        response = self._ntrip_exchange(headers, distributor)
        assert b"ICY 400 Bad Request" in response


@pytest.mark.unit
class TestNTRIPStreaming:
    """Tests for NTRIP data streaming after successful handshake."""

    def _run_ntrip_streaming_test(self, distributor, test_fn):
        """Helper: start NTRIP server, run test_fn, cancel handler tasks."""

        async def _inner():
            handler_tasks = set()

            async def handler(r, w):
                task = asyncio.current_task()
                handler_tasks.add(task)
                try:
                    await keelson2rtcm.handle_ntrip_client(r, w, distributor, "RTCM3")
                finally:
                    handler_tasks.discard(task)

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            distributor.set_loop(asyncio.get_running_loop())

            try:
                await test_fn(port)
            finally:
                # Cancel handler tasks so they don't block shutdown
                for t in handler_tasks:
                    t.cancel()
                if handler_tasks:
                    await asyncio.gather(*handler_tasks, return_exceptions=True)
                server.close()
                await server.wait_closed()

        asyncio.run(_inner())

    def test_ntrip_streams_data_after_handshake(self, distributor):
        """NTRIP client should receive RTCM data after connecting to mountpoint."""

        async def run(port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /RTCM3 HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            assert b"ICY 200 OK" in header

            await asyncio.sleep(0.05)

            test_data = b"\xd3\x00\x13RTCM_TEST_DATA_HERE"
            distributor.distribute(test_data)

            received = await asyncio.wait_for(
                reader.readexactly(len(test_data)), timeout=5
            )
            assert received == test_data

            writer.close()
            await writer.wait_closed()

        self._run_ntrip_streaming_test(distributor, run)

    def test_ntrip_multiple_frames(self, distributor):
        """NTRIP client should receive multiple sequential frames."""

        async def run(port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /RTCM3 HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            await asyncio.sleep(0.05)

            frames = [b"\xd3\x00\x01A", b"\xd3\x00\x01B", b"\xd3\x00\x01C"]
            for frame in frames:
                distributor.distribute(frame)

            total = b"".join(frames)
            received = await asyncio.wait_for(reader.readexactly(len(total)), timeout=5)
            assert received == total

            writer.close()
            await writer.wait_closed()

        self._run_ntrip_streaming_test(distributor, run)


@pytest.mark.unit
class TestTCPClientDisconnect:
    """Tests for TCP client disconnect and cleanup."""

    def test_tcp_client_disconnect_removes_from_distributor(self, distributor):
        """When a TCP client disconnects, its queue is removed."""

        async def _inner():
            handler_tasks = set()
            server = await keelson2rtcm.run_tcp_server(
                distributor, "127.0.0.1", 0, handler_tasks
            )
            port = server.sockets[0].getsockname()[1]
            distributor.set_loop(asyncio.get_running_loop())

            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            # Push data so the handler registers the client
            distributor.distribute(b"\xd3test")
            await asyncio.wait_for(reader.readexactly(5), timeout=5)
            assert distributor.client_count == 1

            # Disconnect the client
            writer.close()
            await writer.wait_closed()

            # Push data repeatedly to trigger the handler's write -> error path.
            # The handler is blocked on queue.get(); once data arrives it tries
            # to write to the closed connection, which raises ConnectionError.
            for _ in range(5):
                distributor.distribute(b"\xd3ping")
                await asyncio.sleep(0.1)
                if distributor.client_count == 0:
                    break

            assert distributor.client_count == 0

            # Cancel remaining handler tasks for clean shutdown
            for t in handler_tasks:
                t.cancel()
            if handler_tasks:
                await asyncio.gather(*handler_tasks, return_exceptions=True)
            server.close()
            await server.wait_closed()

        asyncio.run(_inner())


@pytest.mark.unit
class TestRunServers:
    """Tests for the run_tcp_server / run_ntrip_server helpers."""

    def test_tcp_server_starts_and_accepts(self, distributor):
        """run_tcp_server should accept TCP connections and stream data."""

        async def _inner():
            handler_tasks = set()
            server = await keelson2rtcm.run_tcp_server(
                distributor, "127.0.0.1", 0, handler_tasks
            )
            port = server.sockets[0].getsockname()[1]

            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            # Push data through the distributor — it should reach the client
            distributor.set_loop(asyncio.get_running_loop())
            distributor.distribute(b"\xd3test")

            data = await asyncio.wait_for(reader.readexactly(5), timeout=5)
            assert data == b"\xd3test"

            writer.close()
            await writer.wait_closed()
            for t in handler_tasks:
                t.cancel()
            if handler_tasks:
                await asyncio.gather(*handler_tasks, return_exceptions=True)
            server.close()

        asyncio.run(_inner())

    def test_ntrip_server_starts_and_accepts(self, distributor):
        """run_ntrip_server should return sourcetable on GET /."""

        async def _inner():
            handler_tasks = set()
            server = await keelson2rtcm.run_ntrip_server(
                distributor, "127.0.0.1", 0, "RTCM3", handler_tasks
            )
            port = server.sockets[0].getsockname()[1]

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5)
            assert b"SOURCETABLE 200 OK" in response
            assert b"ENDSOURCETABLE" in response

            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()

        asyncio.run(_inner())


@pytest.mark.unit
class TestShutdownCancelsClients:
    """Test that run_servers cancels client tasks on shutdown."""

    def test_shutdown_cancels_connected_clients(self, distributor):
        """Clients blocked on queue.get() should be cancelled on shutdown."""

        async def _inner():
            from keelson.scaffolding import GracefulShutdown

            # Create a GracefulShutdown without registering signal handlers
            shutdown = GracefulShutdown(signals=[])

            distributor.set_loop(asyncio.get_running_loop())

            # Start run_servers in a task
            server_task = asyncio.create_task(
                keelson2rtcm.run_servers(
                    distributor, "127.0.0.1", 0, None, "RTCM3", shutdown
                )
            )

            # Give server time to start
            await asyncio.sleep(0.1)

            # Signal shutdown
            shutdown.request()

            # Should complete without hanging
            await asyncio.wait_for(server_task, timeout=5)

        asyncio.run(_inner())
