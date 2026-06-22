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

    def __init__(self, port: int, send_on_connect: bytes | None = None):
        self.port = port
        # When set, the server pushes this CoT blob to every connected client
        # repeatedly (~1 Hz), standing in for a TAK server relaying a track.
        self.send_on_connect = send_on_connect
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._thread: threading.Thread | None = None
        self.received: list[bytes] = []
        self._ready = threading.Event()

    async def _handle(self, reader, writer):
        async def _pump():
            try:
                while True:
                    writer.write(self.send_on_connect)
                    await writer.drain()
                    await asyncio.sleep(1.0)
            except (asyncio.CancelledError, ConnectionError):
                pass

        pump_task = asyncio.ensure_future(_pump()) if self.send_on_connect else None
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                self.received.append(data)
        except (asyncio.CancelledError, ConnectionError):
            return
        finally:
            if pump_task is not None:
                pump_task.cancel()

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


@pytest.mark.e2e
def test_cot_event_republishes_to_keelson(connector_process_factory, zenoh_endpoints):
    """tak2keelson should connect to a TAK server, receive a CoT event, and
    republish the mapped fields as keelson subjects under @target/cot_{uid}.

    Guards two easy-to-miss failure modes: the inbound pytak receive wiring
    (regression for the CLITool rx_queue hookup) and the subscriber key
    expression — @v0 and @target are verbatim chunks that ``**`` never matches,
    so a naive ``realm/**`` subscription silently sees nothing.
    """
    import zenoh
    import keelson

    cot_uid = "DUMMY-PHONE-1"
    cot_bytes = (
        '<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" '
        'time="2026-01-01T00:00:00.00Z" start="2026-01-01T00:00:00.00Z" '
        'stale="2099-01-01T00:00:00.00Z">'
        '<point lat="57.706" lon="11.937" hae="12.3" ce="4.5" le="9999999.0"/>'
        '<detail><contact callsign="DUMMYBOAT"/></detail></event>'
    ) % cot_uid
    cot_bytes = cot_bytes.encode()

    tak_port = _free_port()
    server = _DummyTakServer(tak_port, send_on_connect=cot_bytes)
    server.start()

    received: dict[str, bytes] = {}
    try:
        sub_conf = zenoh.Config()
        sub_conf.insert_json5("mode", '"peer"')
        sub_conf.insert_json5("listen/endpoints", f'["{zenoh_endpoints["listen"]}"]')

        with zenoh.open(sub_conf) as session:

            def handler(sample):
                subject = keelson.get_subject_from_pubsub_key(str(sample.key_expr))
                _r, _e, inner = keelson.uncover(bytes(sample.payload.to_bytes()))
                received[subject] = inner

            # @v0 and @target are verbatim chunks; spell them out literally.
            session.declare_subscriber("test-realm/@v0/**/@target/**", handler)

            tak2keelson = connector_process_factory(
                "tak",
                "tak2keelson",
                [
                    "--realm",
                    "test-realm",
                    "--entity-id",
                    "test-vessel",
                    "--source-id",
                    "tak/0",
                    "--mode",
                    "peer",
                    "--connect",
                    zenoh_endpoints["connect"],
                    "--tak-url",
                    f"tcp://127.0.0.1:{tak_port}",
                ],
            )
            tak2keelson.start()
            time.sleep(4)
            assert tak2keelson.is_running(), (
                "tak2keelson exited early. stderr: " f"{tak2keelson.logs()[1][:2000]}"
            )

            for _ in range(10):
                if "location_fix" in received:
                    break
                time.sleep(0.6)

            tak2keelson.stop()

        assert (
            "location_fix" in received
        ), "tak2keelson never published location_fix to the bus."

        from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

        loc = LocationFix()
        loc.ParseFromString(received["location_fix"])
        assert loc.latitude == pytest.approx(57.706, abs=1e-3)
        assert loc.longitude == pytest.approx(11.937, abs=1e-3)

        assert "name" in received, "callsign was not republished as `name`."
        name = keelson.decode_protobuf_payload_from_type_name(
            received["name"], keelson.get_subject_schema("name")
        )
        assert name.value == "DUMMYBOAT"
    finally:
        server.stop()
