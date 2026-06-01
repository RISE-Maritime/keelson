"""Byte-stream transport to the rorkult MCU.

This module is deliberately narrow: it does *not* know about framing,
protocol semantics, or Zenoh. It exposes a `Transport` ABC and a
`TcpTransport` that opens an asyncio TCP connection to the MCU. Reads
raise ``ConnectionError`` on EOF / disconnect; the connector's
supervisor loop is responsible for catching that and reconnecting via
``ReconnectBackoff``.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("rorkult.transport")


class Transport(ABC):
    """Bidirectional byte stream to the MCU.

    Implementations are responsible for `connect()` and `close()`;
    `read()` / `write()` only work between those two. Reads raise
    ``ConnectionError`` on EOF so the supervisor can treat the
    transport as dead and reconnect — never returns an empty bytes.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Open the connection. Raises on failure."""

    @abstractmethod
    async def close(self) -> None:
        """Close the connection. Idempotent; never raises."""

    @abstractmethod
    async def read(self, n: int) -> bytes:
        """Read up to ``n`` bytes. Raises ``ConnectionError`` on EOF."""

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Write all of ``data``. Raises ``ConnectionError`` if not connected."""

    @property
    @abstractmethod
    def connected(self) -> bool: ...


class TcpTransport(Transport):
    """asyncio TCP client to the MCU.

    Stateless across reconnects — supervisor calls ``connect()`` again
    after a drop. Connect uses a bounded timeout so the supervisor
    backoff drives the retry cadence, not the OS default.
    """

    def __init__(self, host: str, port: int, *, connect_timeout_s: float = 5.0):
        self._host = host
        self._port = port
        self._connect_timeout_s = connect_timeout_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def endpoint(self) -> str:
        return f"{self._host}:{self._port}"

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def connect(self) -> None:
        if self.connected:
            return
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=self._connect_timeout_s,
        )

    async def close(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    async def read(self, n: int) -> bytes:
        if self._reader is None:
            raise ConnectionError("transport not connected")
        data = await self._reader.read(n)
        if not data:
            raise ConnectionError(f"MCU closed connection (EOF) at {self.endpoint}")
        return data

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            raise ConnectionError("transport not connected")
        self._writer.write(data)
        await self._writer.drain()


class ReconnectBackoff:
    """Bounded exponential backoff for connection-supervisor reconnect cadence.

    Reset on each successful connect so a flapping link doesn't end up
    waiting the max delay forever after one good session.
    """

    def __init__(self, min_s: float, max_s: float, factor: float = 2.0):
        if min_s <= 0 or max_s < min_s:
            raise ValueError(
                f"reconnect backoff bounds invalid: min={min_s} max={max_s}"
            )
        self._min = min_s
        self._max = max_s
        self._factor = factor
        self._current = min_s

    def reset(self) -> None:
        self._current = self._min

    def next_delay(self) -> float:
        delay = self._current
        self._current = min(self._current * self._factor, self._max)
        return delay
