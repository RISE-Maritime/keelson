#!/usr/bin/env python3

"""End-to-end tests for the TAK / CoT connectors.

Spawn the actual connector binaries as subprocesses against a dummy TCP server
that stands in for a TAK server.
"""

import asyncio
import socket
import threading
import time
import xml.etree.ElementTree as ET

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.e2e
def test_tak2keelson_help_exits_zero(run_connector):
    result = run_connector("tak", "tak2keelson", ["--help"], timeout=15)
    assert result.returncode == 0
    assert "--tak-url" in result.stdout


@pytest.mark.e2e
def test_keelson2tak_help_exits_zero(run_connector):
    result = run_connector("tak", "keelson2tak", ["--help"], timeout=15)
    assert result.returncode == 0
    assert "--cot-uid" in result.stdout


class _DummyTakServer:
    """Tiny asyncio TCP listener that records everything each client sends."""

    def __init__(self, port: int):
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._thread: threading.Thread | None = None
        self.received: list[bytes] = []
        self._ready = threading.Event()

    async def _handle(self, reader, writer):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                self.received.append(data)
        except (asyncio.CancelledError, ConnectionError):
            return

    def start(self):
        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _serve():
                self._server = await asyncio.start_server(
                    self._handle, "127.0.0.1", self.port
                )
                self._ready.set()
                async with self._server:
                    await self._server.serve_forever()

            try:
                self._loop.run_until_complete(_serve())
            except asyncio.CancelledError:
                pass
            except Exception:
                self._ready.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5)

    def stop(self):
        if self._loop and self._server:

            def _close():
                self._server.close()
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()

            self._loop.call_soon_threadsafe(_close)
        if self._thread:
            self._thread.join(timeout=2)


def _extract_our_event(blob: bytes, uid: str):
    """Scan the blob for a ``<event>`` whose ``uid`` matches and return its root."""
    pos = 0
    while True:
        start = blob.find(b"<event", pos)
        if start == -1:
            return None
        end = blob.find(b"</event>", start)
        if end == -1:
            return None
        chunk = blob[start : end + len(b"</event>")]
        try:
            root = ET.fromstring(chunk)
        except ET.ParseError:
            pos = end + 1
            continue
        if root.get("uid") == uid:
            return root
        pos = end + 1


@pytest.mark.e2e
def test_position_roundtrips_to_dummy_tak_server(
    connector_process_factory, zenoh_endpoints
):
    """keelson2tak should connect to a TAK server and emit CoT XML when a
    location_fix is published on zenoh."""
    import zenoh
    import keelson
    from keelson.helpers import enclose_from_lon_lat

    tak_port = _free_port()
    server = _DummyTakServer(tak_port)
    server.start()

    root = None
    try:
        pub_conf = zenoh.Config()
        pub_conf.insert_json5("mode", '"peer"')
        pub_conf.insert_json5("listen/endpoints", f'["{zenoh_endpoints["listen"]}"]')

        with zenoh.open(pub_conf) as session:
            keelson2tak = connector_process_factory(
                "tak",
                "keelson2tak",
                [
                    "--realm",
                    "test-realm",
                    "--entity-id",
                    "test-vessel",
                    "--mode",
                    "peer",
                    "--connect",
                    zenoh_endpoints["connect"],
                    "--tak-url",
                    f"tcp://127.0.0.1:{tak_port}",
                    "--cot-uid",
                    "rise-test-self",
                    "--cot-type",
                    "a-f-S-X",
                    "--cot-callsign",
                    "TESTBOAT",
                    "--cot-stale-seconds",
                    "30",
                    "--emit-at-most-every",
                    "0.0",
                    "--emit-period",
                    "1.0",
                ],
            )
            keelson2tak.start()
            time.sleep(4)
            assert keelson2tak.is_running(), (
                "keelson2tak exited early. stderr: " f"{keelson2tak.logs()[1][:2000]}"
            )

            key = keelson.construct_pubsub_key(
                "test-realm", "test-vessel", "location_fix", "gnss/0"
            )
            for _ in range(10):
                session.put(key, enclose_from_lon_lat(11.937, 57.706))
                blob = b"".join(server.received)
                root = _extract_our_event(blob, "rise-test-self")
                if root is not None:
                    break
                time.sleep(0.6)

            keelson2tak.stop()

        assert root is not None, "Dummy TAK server never received our CoT event."
        point = root.find("point")
        assert float(point.get("lat")) == pytest.approx(57.706, abs=1e-3)
        assert float(point.get("lon")) == pytest.approx(11.937, abs=1e-3)
    finally:
        server.stop()
