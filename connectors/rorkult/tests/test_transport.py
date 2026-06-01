"""Unit tests for rorkult.transport.

Each async scenario is driven through ``asyncio.run`` (no
pytest-asyncio dep). The :class:`MockMcu` fixture from conftest gives
us a real TCP server on localhost.
"""

import asyncio

import pytest

from rorkult.transport import ReconnectBackoff, TcpTransport


# --------------------------------------------------------------------------
# TcpTransport
# --------------------------------------------------------------------------


def test_tcp_transport_connect_then_close(mock_mcu):
    async def scenario():
        t = TcpTransport("127.0.0.1", mock_mcu.port)
        assert not t.connected
        await t.connect()
        assert t.connected
        await t.close()
        assert not t.connected

    asyncio.run(scenario())
    assert mock_mcu.wait_for_connections(1)


def test_tcp_transport_write_lands_on_server(mock_mcu):
    async def scenario():
        t = TcpTransport("127.0.0.1", mock_mcu.port)
        await t.connect()
        try:
            await t.write(b"hello rorkult")
        finally:
            await t.close()

    asyncio.run(scenario())
    # Give the server's read coroutine a beat to drain.
    deadline = 1.0
    waited = 0.0
    while bytes(mock_mcu.received) != b"hello rorkult" and waited < deadline:
        import time

        time.sleep(0.05)
        waited += 0.05
    assert bytes(mock_mcu.received) == b"hello rorkult"


def test_tcp_transport_read_echo(mock_mcu_echo):
    async def scenario():
        t = TcpTransport("127.0.0.1", mock_mcu_echo.port)
        await t.connect()
        try:
            await t.write(b"ping")
            data = await asyncio.wait_for(t.read(4096), timeout=2.0)
            assert data == b"ping"
        finally:
            await t.close()

    asyncio.run(scenario())


def test_tcp_transport_read_raises_on_server_disconnect(mock_mcu):
    async def scenario():
        t = TcpTransport("127.0.0.1", mock_mcu.port)
        await t.connect()
        try:
            # The server's accept callback bumps the connection counter
            # on its own loop thread; without waiting, ``disconnect_client``
            # can race in *before* the server has stashed the writer and
            # become a silent no-op. Block until the server has the
            # connection in hand.
            assert mock_mcu.wait_for_connections(1)
            mock_mcu.disconnect_client()
            with pytest.raises(ConnectionError):
                await asyncio.wait_for(t.read(4096), timeout=2.0)
        finally:
            await t.close()

    asyncio.run(scenario())


def test_tcp_transport_connect_to_unreachable_port_raises():
    async def scenario():
        # Port 1 is virtually always refused on a sandboxed loopback;
        # if a CI quirk binds it, the test would false-positive — but
        # in practice this is reliable.
        t = TcpTransport("127.0.0.1", 1, connect_timeout_s=0.5)
        with pytest.raises((ConnectionError, OSError, asyncio.TimeoutError)):
            await t.connect()

    asyncio.run(scenario())


def test_tcp_transport_write_without_connect_raises():
    async def scenario():
        t = TcpTransport("127.0.0.1", 1)
        with pytest.raises(ConnectionError):
            await t.write(b"x")

    asyncio.run(scenario())


def test_tcp_transport_read_without_connect_raises():
    async def scenario():
        t = TcpTransport("127.0.0.1", 1)
        with pytest.raises(ConnectionError):
            await t.read(1)

    asyncio.run(scenario())


def test_tcp_transport_close_is_idempotent():
    async def scenario():
        t = TcpTransport("127.0.0.1", 1)
        await t.close()  # never connected, no-op
        await t.close()  # second close, also no-op

    asyncio.run(scenario())


def test_tcp_transport_connect_twice_is_a_no_op(mock_mcu):
    async def scenario():
        t = TcpTransport("127.0.0.1", mock_mcu.port)
        await t.connect()
        try:
            await t.connect()  # already connected: should not raise
            assert t.connected
        finally:
            await t.close()

    asyncio.run(scenario())
    # Only one accept should have happened (wait_for_connections handles
    # the cross-thread race between client-side connect returning and
    # the server's accept callback running).
    assert mock_mcu.wait_for_connections(1)
    assert mock_mcu.connections == 1


# --------------------------------------------------------------------------
# ReconnectBackoff
# --------------------------------------------------------------------------


def test_backoff_grows_then_caps():
    b = ReconnectBackoff(min_s=1.0, max_s=8.0, factor=2.0)
    delays = [b.next_delay() for _ in range(6)]
    # 1, 2, 4, 8, 8, 8 — grows by factor then caps at max
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_backoff_reset_returns_to_min():
    b = ReconnectBackoff(min_s=1.0, max_s=8.0)
    [b.next_delay() for _ in range(4)]
    b.reset()
    assert b.next_delay() == 1.0


def test_backoff_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        ReconnectBackoff(min_s=0.0, max_s=1.0)
    with pytest.raises(ValueError):
        ReconnectBackoff(min_s=-1.0, max_s=1.0)
    with pytest.raises(ValueError):
        ReconnectBackoff(min_s=5.0, max_s=1.0)
