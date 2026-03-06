#!/usr/bin/env python3
"""Keelson to RTCM v3 distribution connector.

Subscribes to RTCM v3 data on the keelson bus and serves it to rovers via
bare TCP and/or NTRIP v1 server.  Both servers can run simultaneously on
separate ports, sharing the same RTCMDistributor and asyncio event loop.
"""

import asyncio
import logging
import argparse
import threading

import zenoh

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedBytes
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    setup_logging,
    GracefulShutdown,
)

logger = logging.getLogger("keelson2rtcm")


class RTCMDistributor:
    """Thread-safe bridge between zenoh subscriber callback and asyncio server.

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


def on_rtcm_sample(sample, distributor: RTCMDistributor) -> None:
    """Zenoh subscriber callback — extract raw bytes and distribute."""
    try:
        _received_at, _enclosed_at, payload_bytes = keelson.uncover(
            sample.payload.to_bytes()
        )
        tb = TimestampedBytes.FromString(payload_bytes)
        distributor.distribute(tb.value)
    except Exception:
        logger.exception("Error processing RTCM sample")


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


async def handle_tcp_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    distributor: RTCMDistributor,
) -> None:
    """Handle a bare TCP client — stream raw RTCM bytes immediately."""
    addr = writer.get_extra_info("peername")
    logger.info("TCP client connected: %s", addr)
    queue = distributor.add_client()
    try:
        while True:
            data = await queue.get()
            writer.write(data)
            await asyncio.wait_for(writer.drain(), timeout=_WRITE_TIMEOUT)
    except (ConnectionError, asyncio.CancelledError):
        pass
    except asyncio.TimeoutError:
        logger.warning("TCP client %s stale (write timeout), disconnecting", addr)
    finally:
        distributor.remove_client(queue)
        writer.close()
        await writer.wait_closed()
        logger.info("TCP client disconnected: %s", addr)


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
    binding to ``127.0.0.1`` (``--server-host``).
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


async def run_tcp_server(
    distributor: RTCMDistributor,
    host: str,
    port: int,
    client_tasks: set[asyncio.Task],
) -> asyncio.Server:
    """Start a bare TCP server and return the ``asyncio.Server``."""

    async def client_cb(r, w):
        task = asyncio.current_task()
        client_tasks.add(task)
        try:
            await handle_tcp_client(r, w, distributor)
        finally:
            client_tasks.discard(task)

    server = await asyncio.start_server(client_cb, host, port)
    logger.info("TCP server listening on %s:%d", host, port)
    return server


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


async def run_servers(
    distributor: RTCMDistributor,
    host: str,
    tcp_port: int | None,
    ntrip_port: int | None,
    mountpoint: str,
    shutdown: GracefulShutdown,
) -> None:
    """Start configured servers and block until shutdown."""

    distributor.set_loop(asyncio.get_running_loop())

    client_tasks: set[asyncio.Task] = set()
    servers: list[asyncio.Server] = []
    if tcp_port is not None:
        servers.append(await run_tcp_server(distributor, host, tcp_port, client_tasks))
    if ntrip_port is not None:
        servers.append(
            await run_ntrip_server(
                distributor, host, ntrip_port, mountpoint, client_tasks
            )
        )

    # Wait for shutdown in a non-blocking way
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, shutdown.wait)

    # Stop accepting new connections
    for server in servers:
        server.close()
    for server in servers:
        await server.wait_closed()

    # Cancel all active client handler tasks
    for task in client_tasks:
        task.cancel()
    if client_tasks:
        await asyncio.gather(*client_tasks, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(
        description="Keelson to RTCM v3 distribution connector"
    )
    add_common_arguments(parser)
    parser.add_argument(
        "-r", "--realm", required=True, type=str, help="Keelson realm (base path)"
    )
    parser.add_argument(
        "-e", "--entity-id", required=True, type=str, help="Entity identifier"
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default="**",
        help="Source identifier to subscribe to (default: ** for all)",
    )
    parser.add_argument(
        "--server-host",
        type=str,
        default="0.0.0.0",
        help="Host to bind server to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=None,
        help="Port for bare TCP server (raw RTCM stream)",
    )
    parser.add_argument(
        "--ntrip-port",
        type=int,
        default=None,
        help="Port for NTRIP v1 server",
    )
    parser.add_argument(
        "--mountpoint",
        type=str,
        default="RTCM3",
        help="NTRIP mountpoint name (default: RTCM3)",
    )

    args = parser.parse_args()

    if args.tcp_port is None and args.ntrip_port is None:
        parser.error("At least one of --tcp-port or --ntrip-port is required")
    if (
        args.tcp_port is not None
        and args.ntrip_port is not None
        and args.tcp_port == args.ntrip_port
    ):
        parser.error("--tcp-port and --ntrip-port must be different")
    setup_logging(level=args.log_level)

    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)

    key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, "raw_rtcm_v3", args.source_id
    )

    distributor = RTCMDistributor()

    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)

    subscriber = session.declare_subscriber(
        key, lambda sample: on_rtcm_sample(sample, distributor)
    )
    logger.info("Subscribed to: %s", key)

    with GracefulShutdown() as shutdown:
        asyncio.run(
            run_servers(
                distributor,
                args.server_host,
                args.tcp_port,
                args.ntrip_port,
                args.mountpoint,
                shutdown,
            )
        )

    subscriber.undeclare()
    session.close()
    logger.info("Shut down.")


if __name__ == "__main__":
    main()
