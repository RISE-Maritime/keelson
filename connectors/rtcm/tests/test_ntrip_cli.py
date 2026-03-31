#!/usr/bin/env python3

"""Tests for ntrip-cli.py."""

import io
import asyncio
import threading

import pytest

from conftest import ntrip_cli


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
        result = ntrip_cli.build_sourcetable("RTCM3")
        assert result.startswith("STR;RTCM3;")

    def test_contains_endsourcetable(self):
        result = ntrip_cli.build_sourcetable("RTCM3")
        assert "ENDSOURCETABLE" in result

    def test_custom_mountpoint(self):
        result = ntrip_cli.build_sourcetable("MY_MOUNT")
        assert "STR;MY_MOUNT;" in result

    def test_ends_with_crlf(self):
        result = ntrip_cli.build_sourcetable("RTCM3")
        assert result.endswith("\r\n")


@pytest.mark.unit
class TestNTRIPProtocol:
    """Tests for NTRIP protocol responses using a real asyncio TCP server."""

    def _ntrip_exchange(self, request_bytes, distributor, mountpoint="RTCM3"):
        """Start NTRIP server, send request, return response bytes."""

        async def _inner():
            async def handler(r, w):
                await ntrip_cli.handle_ntrip_client(r, w, distributor, mountpoint)

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
                    await ntrip_cli.handle_ntrip_client(r, w, distributor, "RTCM3")
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
class TestStdinReaderThread:
    """Tests for the stdin reader thread function."""

    def test_reads_and_distributes(self):
        """stdin_reader_thread should read chunks and call distributor.distribute."""
        from unittest.mock import Mock, patch

        distributor = ntrip_cli.RTCMDistributor()
        queue = distributor.add_client()
        shutdown_event = threading.Event()

        test_data = b"\xd3\x00\x13RTCM_DATA_HERE_1234"

        fake_stdin = Mock()
        fake_stdin.buffer = io.BytesIO(test_data)

        with patch.object(ntrip_cli, "sys", wraps=ntrip_cli.sys) as mock_sys:
            mock_sys.stdin = fake_stdin
            ntrip_cli.stdin_reader_thread(distributor, shutdown_event)

        # Thread should have set shutdown (EOF)
        assert shutdown_event.is_set()

        # Data should have been distributed
        received = queue.get_nowait()
        assert received == test_data

    def test_sets_shutdown_on_eof(self):
        """stdin_reader_thread should set shutdown_event when stdin reaches EOF."""
        from unittest.mock import Mock, patch

        distributor = ntrip_cli.RTCMDistributor()
        shutdown_event = threading.Event()

        fake_stdin = Mock()
        fake_stdin.buffer = io.BytesIO(b"")

        with patch.object(ntrip_cli, "sys", wraps=ntrip_cli.sys) as mock_sys:
            mock_sys.stdin = fake_stdin
            ntrip_cli.stdin_reader_thread(distributor, shutdown_event)

        assert shutdown_event.is_set()
