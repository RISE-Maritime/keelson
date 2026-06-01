"""Shared fixtures for keelson2rorkult connector tests."""

import asyncio
import importlib.util
import pathlib
import socket
import sys
import threading
from importlib.machinery import SourceFileLoader

import pytest

# Make the rorkult sub-package importable for unit tests (matches the
# in-bin sys.path bootstrap so tests and the running connector see the
# same modules).
PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

BIN_ROOT = PKG_ROOT / "bin"


def _load_bin_module():
    """Load bin/keelson2rorkult.py as a Python module (it is a standalone
    executable, not part of a package). Used by CLI unit tests that want
    to call into ``main`` / ``build_arg_parser`` directly."""
    path = BIN_ROOT / "keelson2rorkult.py"
    loader = SourceFileLoader("keelson2rorkult", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def keelson2rorkult():
    """Loaded keelson2rorkult bin module."""
    return _load_bin_module()


# --------------------------------------------------------------------------
# Mock MCU TCP server: a tiny asyncio listener that accepts a single
# connection, optionally echoes received bytes, and tracks connections.
#
# Lives on a dedicated thread with its own event loop so synchronous
# tests can interact with it without juggling pytest-asyncio.
# --------------------------------------------------------------------------


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockMcu:
    """A trivial TCP server suitable for transport-layer tests.

    Counts accepted connections and stores received bytes. Operating
    modes:
      - ``echo=True``  : every received chunk is echoed back as-is
      - ``echo=False`` : bytes are stored, never echoed (sink mode)

    Use ``send(bytes)`` to push data to the most-recently-accepted
    client; useful for the supervisor "MCU -> connector" read path.
    """

    def __init__(self, *, echo: bool = False):
        self._echo = echo
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._writer: asyncio.StreamWriter | None = None
        self.port: int = 0
        self.connections: int = 0
        self.received: bytearray = bytearray()

    def start(self) -> None:
        self.port = _free_tcp_port()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def disconnect_client(self) -> None:
        """Forcibly drop the active client connection (simulate MCU drop).

        Uses ``transport.abort()`` rather than ``writer.close()`` so the
        FIN/RST goes out immediately — ``close()`` is graceful and the
        client-side read can otherwise sit on a stale socket for the
        kernel's TCP linger window.
        """
        loop = self._loop
        writer = self._writer
        if loop is None or writer is None:
            return

        def _abort():
            try:
                writer.transport.abort()
            except Exception:
                pass

        loop.call_soon_threadsafe(_abort)

    def wait_for_connections(self, n: int, timeout: float = 2.0) -> bool:
        """Block until ``self.connections >= n`` or ``timeout`` elapses.

        The accept callback runs on the server's own loop thread, so a
        client-side ``await open_connection`` can return before the
        server's _handle has bumped the counter. Tests that assert on
        the count should call this first.
        """
        import time as _time

        deadline = _time.time() + timeout
        while _time.time() < deadline:
            if self.connections >= n:
                return True
            _time.sleep(0.02)
        return False

    def send(self, data: bytes) -> None:
        """Send bytes to the active client from outside the event loop."""
        loop = self._loop
        writer = self._writer
        if loop is None or writer is None:
            raise RuntimeError("MockMcu has no connected client")

        def _do_send():
            writer.write(data)

        loop.call_soon_threadsafe(_do_send)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_server())
            self._ready.set()
            self._loop.run_forever()
        finally:
            if self._server is not None:
                self._server.close()
            self._loop.close()

    async def _start_server(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self._writer = writer
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self.received.extend(data)
                if self._echo:
                    writer.write(data)
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
            self._writer = None


@pytest.fixture
def mock_mcu():
    """Sink-mode MockMcu, started for the test, stopped on teardown."""
    server = MockMcu(echo=False)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_mcu_echo():
    """Echo-mode MockMcu, started for the test, stopped on teardown."""
    server = MockMcu(echo=True)
    server.start()
    yield server
    server.stop()
