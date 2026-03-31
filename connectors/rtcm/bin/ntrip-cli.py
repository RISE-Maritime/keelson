#!/usr/bin/env python3
"""NTRIP v1 server that reads RTCM data from stdin and serves to clients.

Reads raw RTCM bytes from stdin and distributes them to connected NTRIP v1
clients.  This is a standalone tool with no Zenoh dependency.

Typical usage:

    keelson2rtcm -r realm -e gnss | ntrip-cli --port 2101 --mountpoint RTCM3
"""

import sys
import signal
import asyncio
import logging
import argparse
import threading

logger = logging.getLogger("ntrip-cli")


class RTCMDistributor:
    """Thread-safe bridge between stdin reader thread and asyncio NTRIP server.

    Maintains a set of asyncio queues, one per connected client. The
    ``distribute`` method is safe to call from any thread — it wakes the
    asyncio event loop via ``call_soon_threadsafe`` after enqueuing data.
    """

    def __init__(self):
        self._clients: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the asyncio event loop for thread-safe wakeup."""
        self._loop = loop

    def add_client(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._clients.add(queue)
        return queue

    def remove_client(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._clients.discard(queue)

    def distribute(self, data: bytes) -> None:
        with self._lock:
            for queue in self._clients:
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    logger.warning("Client queue full, dropping frame")
        # Wake the asyncio event loop — put_nowait from a non-asyncio thread
        # adds data to the queue but doesn't notify the selector.
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass  # Event loop already closed

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


def build_sourcetable(mountpoint: str) -> str:
    """Build an NTRIP v1 sourcetable response body."""
    # Minimal STR record per NTRIP v1 spec
    str_record = (
        f"STR;{mountpoint};{mountpoint};RTCM 3;;2;GPS;;"
        f";;;0.00;0.00;0;0;keelson;none;N;N;;\r\n"
    )
    return str_record + "ENDSOURCETABLE\r\n"


_MAX_NTRIP_HEADERS = 32
_WRITE_TIMEOUT = 30.0


async def handle_ntrip_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    distributor: RTCMDistributor,
    mountpoint: str,
) -> None:
    """Handle an NTRIP v1 client.

    Note: This is a minimal NTRIP v1 server implementation.  It does NOT
    support authentication (``Authorization`` headers are ignored).  Access
    control should be handled at the network level (firewall, VPN) or by
    binding to ``127.0.0.1`` (``--host``).
    """
    addr = writer.get_extra_info("peername")
    logger.info("NTRIP client connected: %s", addr)

    try:
        # Read the request line
        request_line = await asyncio.wait_for(reader.readline(), timeout=10)
        request_str = request_line.decode("ascii", errors="replace").strip()
        logger.debug("Request: %s", request_str)

        # Read and discard remaining headers (limit count to prevent abuse)
        for _ in range(_MAX_NTRIP_HEADERS):
            header_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if header_line.strip() == b"":
                break
        else:
            logger.warning("NTRIP client %s sent too many headers, dropping", addr)
            writer.write(b"ICY 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        # Parse method and path
        parts = request_str.split()
        if len(parts) < 2:
            writer.write(b"ICY 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        path = parts[1]

        # Sourcetable request
        if path == "/":
            sourcetable = build_sourcetable(mountpoint)
            response = (
                "SOURCETABLE 200 OK\r\n"
                f"Content-Length: {len(sourcetable)}\r\n"
                "Content-Type: text/plain\r\n"
                "\r\n"
                f"{sourcetable}"
            )
            writer.write(response.encode("ascii"))
            await writer.drain()
            return

        # Stream request
        requested_mount = path.lstrip("/")
        if requested_mount != mountpoint:
            writer.write(b"ICY 404 Not Found\r\n\r\n")
            await writer.drain()
            return

        # Valid mountpoint — start streaming
        writer.write(b"ICY 200 OK\r\n\r\n")
        await writer.drain()

        queue = distributor.add_client()
        try:
            while True:
                data = await queue.get()
                writer.write(data)
                await asyncio.wait_for(writer.drain(), timeout=_WRITE_TIMEOUT)
        except (ConnectionError, asyncio.CancelledError):
            pass
        except asyncio.TimeoutError:
            logger.warning("NTRIP client %s stale (write timeout), disconnecting", addr)
        finally:
            distributor.remove_client(queue)

    except (asyncio.TimeoutError, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()
        logger.info("NTRIP client disconnected: %s", addr)


async def run_ntrip_server(
    distributor: RTCMDistributor,
    host: str,
    port: int,
    mountpoint: str,
    client_tasks: set[asyncio.Task],
) -> asyncio.Server:
    """Start an NTRIP v1 server and return the ``asyncio.Server``."""

    async def client_cb(r, w):
        task = asyncio.current_task()
        client_tasks.add(task)
        try:
            await handle_ntrip_client(r, w, distributor, mountpoint)
        finally:
            client_tasks.discard(task)

    server = await asyncio.start_server(client_cb, host, port)
    logger.info("NTRIP server listening on %s:%d", host, port)
    return server


def stdin_reader_thread(
    distributor: RTCMDistributor,
    shutdown_event: threading.Event,
) -> None:
    """Read raw bytes from stdin in a background thread and distribute."""
    try:
        while not shutdown_event.is_set():
            chunk = sys.stdin.buffer.read1(4096)
            if not chunk:
                break
            distributor.distribute(chunk)
    except OSError:
        pass
    finally:
        shutdown_event.set()


async def run(
    distributor: RTCMDistributor,
    host: str,
    port: int,
    mountpoint: str,
) -> None:
    """Start NTRIP server and stdin reader, block until shutdown."""
    loop = asyncio.get_running_loop()
    distributor.set_loop(loop)

    shutdown_event = threading.Event()

    # Install signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    client_tasks: set[asyncio.Task] = set()
    server = await run_ntrip_server(distributor, host, port, mountpoint, client_tasks)

    reader_thread = threading.Thread(
        target=stdin_reader_thread,
        args=(distributor, shutdown_event),
        daemon=True,
    )
    reader_thread.start()

    # Wait for shutdown (stdin EOF or signal)
    await loop.run_in_executor(None, shutdown_event.wait)

    # Cleanup
    server.close()
    await server.wait_closed()
    for task in client_tasks:
        task.cancel()
    if client_tasks:
        await asyncio.gather(*client_tasks, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(
        description="NTRIP v1 server (reads RTCM from stdin)"
    )
    parser.add_argument("--port", type=int, required=True, help="NTRIP server port")
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Bind host (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--mountpoint",
        type=str,
        default="RTCM3",
        help="NTRIP mountpoint name (default: RTCM3)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        help="Log level (default: WARNING)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    distributor = RTCMDistributor()
    asyncio.run(run(distributor, args.host, args.port, args.mountpoint))


if __name__ == "__main__":
    main()
