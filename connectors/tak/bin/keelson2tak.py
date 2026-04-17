#!/usr/bin/env python3

"""
Zenoh -> TAK server connector for Keelson.

Subscribes to keelson subjects describing the local entity, assembles CoT XML
events, and writes them to a TAK server over TCP or TLS.

Uses skarv for state aggregation: a CoT event is emitted whenever location_fix
is updated, or on a periodic timer to keep tracks alive on TAK clients.
"""

import asyncio
import json
import logging
import ssl
import time
import threading
import argparse
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

import zenoh
import skarv
import skarv.utilities
import skarv.middlewares
from skarv.utilities.zenoh import mirror
import keelson

logger = logging.getLogger("keelson2tak")

ARGS: argparse.Namespace = None

# Asyncio event loop running in a background thread
_loop: asyncio.AbstractEventLoop = None
_loop_lock = threading.Lock()

# The active TCP/TLS writer; None when disconnected
_writer: asyncio.StreamWriter = None
_writer_lock: asyncio.Lock = None  # created inside the event loop


def _get_writer_lock() -> asyncio.Lock:
    """Get or create the asyncio writer lock (must be called from within the loop)."""
    global _writer_lock
    if _writer_lock is None:
        _writer_lock = asyncio.Lock()
    return _writer_lock


def _unpack(sample):
    """Unpack a skarv sample into a decoded keelson protobuf message."""
    _, _, payload = keelson.uncover(sample.value.to_bytes())
    return keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(sample.key_expr)
    )


SUBJECTS = [
    "location_fix",
    "location_fix_accuracy_horizontal_m",
    "location_fix_accuracy_vertical_m",
    "course_over_ground_deg",
    "speed_over_ground_knots",
    "name",
]


def build_cot_xml(args: argparse.Namespace, now_utc: datetime = None) -> bytes | None:
    """Assemble a CoT XML event from the current skarv state.

    Returns:
        UTF-8 encoded CoT XML bytes, or None if location_fix is not available.
    """
    now = now_utc or datetime.now(timezone.utc)
    stale = now + timedelta(seconds=args.cot_stale_seconds)

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    time_str = now.strftime(fmt)
    stale_str = stale.strftime(fmt)

    # location_fix is required
    loc_sample = skarv.get("location_fix")
    if not loc_sample:
        logger.debug("No location_fix in skarv vault, skipping CoT emission")
        return None

    loc_fix = _unpack(loc_sample)
    lat = loc_fix.latitude
    lon = loc_fix.longitude
    hae = loc_fix.altitude if loc_fix.altitude else 0.0

    # Optional accuracy fields
    ce = 9999999.0
    ce_sample = skarv.get("location_fix_accuracy_horizontal_m")
    if ce_sample:
        ce = _unpack(ce_sample).value

    le = 9999999.0
    le_sample = skarv.get("location_fix_accuracy_vertical_m")
    if le_sample:
        le = _unpack(le_sample).value

    # Optional course
    course = None
    course_sample = skarv.get("course_over_ground_deg")
    if course_sample:
        course = _unpack(course_sample).value

    # Optional speed (stored in knots, CoT uses m/s)
    speed_mps = None
    speed_sample = skarv.get("speed_over_ground_knots")
    if speed_sample:
        speed_mps = _unpack(speed_sample).value / 1.94384

    # Callsign: prefer keelson 'name', fall back to CLI flag
    callsign = args.cot_callsign or ""
    name_sample = skarv.get("name")
    if name_sample:
        callsign = _unpack(name_sample).value

    # Build XML tree
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", args.cot_uid)
    event.set("type", args.cot_type)
    event.set("time", time_str)
    event.set("start", time_str)
    event.set("stale", stale_str)
    event.set("how", args.cot_how)

    point = ET.SubElement(event, "point")
    point.set("lat", str(lat))
    point.set("lon", str(lon))
    point.set("hae", str(hae))
    point.set("ce", str(ce))
    point.set("le", str(le))

    detail = ET.SubElement(event, "detail")

    if callsign:
        contact = ET.SubElement(detail, "contact")
        contact.set("callsign", callsign)
        contact.set("endpoint", "*:-1:stcp")

    if course is not None or speed_mps is not None:
        track = ET.SubElement(detail, "track")
        if course is not None:
            track.set("course", str(course))
        if speed_mps is not None:
            track.set("speed", str(speed_mps))

    takv = ET.SubElement(detail, "takv")
    takv.set("platform", "keelson-connector-tak")
    takv.set("version", "0.1.0")

    return ET.tostring(event, encoding="unicode").encode("utf-8")


async def _send_cot(xml_bytes: bytes):
    """Write a CoT XML event to the TAK server writer if connected."""
    global _writer
    async with _get_writer_lock():
        if _writer is None:
            logger.debug("No active TAK connection, dropping CoT event")
            return
        try:
            _writer.write(xml_bytes)
            await _writer.drain()
            logger.debug("Sent CoT event (%d bytes)", len(xml_bytes))
        except Exception as exc:
            logger.warning("Failed to send CoT event: %s", exc)
            _writer = None


def _emit_cot():
    """Synchronous wrapper: build CoT XML and schedule a send on the event loop."""
    global _loop
    if ARGS is None:
        return
    xml_bytes = build_cot_xml(ARGS)
    if xml_bytes is None:
        return

    with _loop_lock:
        loop = _loop

    if loop is None or not loop.is_running():
        logger.debug("Event loop not running, dropping CoT event")
        return

    asyncio.run_coroutine_threadsafe(_send_cot(xml_bytes), loop)


@skarv.trigger("location_fix")
def _on_location_fix():
    """Triggered by skarv when location_fix is updated. Emits a CoT event."""
    _emit_cot()


async def _connect_tak(
    host: str,
    port: int,
    use_tls: bool,
    client_cert: str | None,
    client_key: str | None,
    ca_file: str | None,
    insecure: bool,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TCP or TLS connection to a TAK server."""
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if insecure:
            logger.warning(
                "--tak-insecure: TLS certificate verification is DISABLED. "
                "This is insecure and should only be used for testing."
            )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.check_hostname = True
            if ca_file:
                ctx.load_verify_locations(ca_file)
            else:
                ctx.load_default_certs()

        if client_cert:
            key_file = client_key or client_cert
            ctx.load_cert_chain(certfile=client_cert, keyfile=key_file)

        reader, writer = await asyncio.open_connection(host, port, ssl=ctx)
    else:
        reader, writer = await asyncio.open_connection(host, port)

    return reader, writer


async def _run_async(args: argparse.Namespace):
    """Main async loop: connect to TAK server and maintain the connection."""
    global _writer, _loop, _writer_lock

    url = args.tak_url
    if url.startswith("tcp://"):
        host_port = url[len("tcp://") :]
        use_tls = False
    elif url.startswith("tls://"):
        host_port = url[len("tls://") :]
        use_tls = True
    else:
        raise ValueError(f"Unsupported TAK URL scheme: {url!r}. Use tcp:// or tls://")

    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 8089 if use_tls else 8087

    with _loop_lock:
        _loop = asyncio.get_running_loop()
    # Create the asyncio lock inside the running event loop
    _writer_lock = asyncio.Lock()

    while True:
        logger.info("Connecting to TAK server at %s:%d (tls=%s)", host, port, use_tls)
        try:
            reader, writer = await _connect_tak(
                host,
                port,
                use_tls,
                args.tak_client_cert,
                args.tak_client_key,
                args.tak_ca,
                args.tak_insecure,
            )
            logger.info("Connected to TAK server")
            async with _get_writer_lock():
                _writer = writer

            # Keep the connection alive, drain incoming data (server messages
            # on the outbound side are typically just acknowledgements or
            # SA from other clients — we discard them here).
            try:
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        logger.info("TAK server closed the connection")
                        break
            finally:
                async with _get_writer_lock():
                    _writer = None
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        except (ConnectionRefusedError, OSError, ssl.SSLError) as exc:
            logger.warning(
                "Connection to TAK server failed: %s. Retrying in %.1fs",
                exc,
                args.reconnect_delay,
            )
        except Exception as exc:
            logger.exception("Unexpected error in TAK connection: %s", exc)

        async with _get_writer_lock():
            _writer = None

        logger.info(
            "Disconnected from TAK server. Reconnecting in %.1fs",
            args.reconnect_delay,
        )
        await asyncio.sleep(args.reconnect_delay)


def _run_event_loop(args: argparse.Namespace):
    """Run the asyncio event loop in a background thread."""
    asyncio.run(_run_async(args))


def main():
    parser = argparse.ArgumentParser(
        prog="keelson2tak",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--log-level", type=int, default=logging.INFO)

    parser.add_argument(
        "--mode",
        "-m",
        dest="mode",
        choices=["peer", "client"],
        type=str,
        help="The Zenoh session mode.",
    )

    parser.add_argument(
        "--connect",
        action="append",
        type=str,
        help="Endpoints to connect to, e.g. tcp/localhost:7447",
    )

    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)

    # TAK server connection
    parser.add_argument(
        "--tak-url",
        type=str,
        required=True,
        help="TAK server URL, e.g. tcp://host:8087 or tls://host:8089",
    )
    parser.add_argument(
        "--tak-client-cert",
        type=str,
        default=None,
        help="Client certificate PEM file for mutual TLS",
    )
    parser.add_argument(
        "--tak-client-key",
        type=str,
        default=None,
        help="Client key PEM file. If omitted, --tak-client-cert is used for both",
    )
    parser.add_argument(
        "--tak-ca",
        type=str,
        default=None,
        help="CA bundle PEM for verifying the server. Defaults to system trust store",
    )
    parser.add_argument(
        "--tak-insecure",
        action="store_true",
        default=False,
        help="Skip TLS hostname and certificate verification (insecure)",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=5.0,
        help="Seconds to wait between reconnect attempts",
    )

    # keelson2tak specific
    parser.add_argument(
        "--cot-uid",
        type=str,
        required=True,
        help="Globally unique, stable CoT UID for this entity",
    )
    parser.add_argument(
        "--cot-type",
        type=str,
        default="a-f-S-X",
        help="CoT type code for this entity",
    )
    parser.add_argument(
        "--cot-callsign",
        type=str,
        default=None,
        help="Fallback callsign if the 'name' subject is not being published",
    )
    parser.add_argument(
        "--cot-how",
        type=str,
        default="m-g",
        help="CoT provenance code",
    )
    parser.add_argument(
        "--cot-stale-seconds",
        type=float,
        default=60.0,
        help="Seconds after emission time at which TAK clients should treat the track as stale",
    )
    parser.add_argument(
        "--emit-at-most-every",
        type=float,
        default=1.0,
        help="Throttle: minimum seconds between CoT emissions triggered by location_fix updates",
    )
    parser.add_argument(
        "--emit-period",
        type=float,
        default=30.0,
        help="Periodic forced emission interval in seconds",
    )

    for subject in SUBJECTS:
        parser.add_argument(f"--source_id_{subject}", type=str, default="**")

    global ARGS
    ARGS = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=ARGS.log_level
    )
    logging.captureWarnings(True)

    conf = zenoh.Config()
    if ARGS.mode is not None:
        conf.insert_json5("mode", json.dumps(ARGS.mode))
    if ARGS.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(ARGS.connect))

    # Register throttle middleware on location_fix
    logger.info(
        "Registering throttle middleware on location_fix (at most every %.1fs)",
        ARGS.emit_at_most_every,
    )
    skarv.register_middleware(
        "location_fix", skarv.middlewares.throttle(ARGS.emit_at_most_every)
    )

    # Set up periodic forced emission
    logger.info("Periodic CoT emission every %.1fs", ARGS.emit_period)
    skarv.utilities.call_every(ARGS.emit_period, wait_first=True)(_emit_cot)

    # Start the asyncio event loop in a background thread
    loop_thread = threading.Thread(target=_run_event_loop, args=(ARGS,), daemon=True)
    loop_thread.start()

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        for subject in SUBJECTS:
            mirror(
                session,
                keelson.construct_pubsub_key(
                    ARGS.realm,
                    ARGS.entity_id,
                    subject,
                    getattr(ARGS, f"source_id_{subject}"),
                ),
                subject,
            )

        while True:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received, shutting down...")
                break


if __name__ == "__main__":
    main()
