#!/usr/bin/env python3

"""
TAK server -> Zenoh connector for Keelson.

Connects to a TAK server (TCP or TLS), receives CoT XML events, and publishes
per-target keelson subjects under the @target/cot_{sanitized_uid} extension.
"""

import asyncio
import json
import logging
import re
import ssl
import time
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import zenoh
import keelson
from keelson.helpers import (
    enclose_from_bytes,
    enclose_from_float,
    enclose_from_string,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.scaffolding import declare_liveliness_token

logger = logging.getLogger("tak2keelson")

# CoT "unknown" sentinel values
COT_UNKNOWN_CE_LE = 9999999.0

PUBLISHERS: dict[str, zenoh.Publisher] = {}


def sanitize_uid(uid: str) -> str:
    """Sanitize a CoT UID for use as a keelson target_id.

    Replaces any character outside [a-zA-Z0-9_-] with '_'.
    """
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", uid)


def parse_cot_event(xml_bytes: bytes, timestamp: int = None):
    """Parse a CoT XML event and yield (subject, envelope) pairs.

    Args:
        xml_bytes: Raw XML bytes for a single CoT <event> element.
        timestamp: Optional nanosecond timestamp; defaults to time.time_ns().

    Yields:
        Tuples of (subject_name, envelope_bytes).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("Failed to parse CoT XML: %s", exc)
        return

    if root.tag != "event":
        logger.debug("Skipping non-event XML tag: %s", root.tag)
        return

    point = root.find("point")
    if point is None:
        logger.debug("CoT event has no <point> element, skipping")
        return

    ts = timestamp or time.time_ns()

    # location_fix: lat, lon, hae (altitude above ellipsoid)
    try:
        lat = float(point.attrib["lat"])
        lon = float(point.attrib["lon"])
        hae = float(point.attrib.get("hae", "0.0"))
    except (KeyError, ValueError) as exc:
        logger.debug("Missing or invalid lat/lon in CoT point: %s", exc)
        return

    loc_payload = LocationFix()
    loc_payload.timestamp.FromNanoseconds(ts)
    loc_payload.latitude = lat
    loc_payload.longitude = lon
    loc_payload.altitude = hae
    yield "location_fix", keelson.enclose(loc_payload.SerializeToString())

    # ce: horizontal 1-sigma accuracy in metres; skip sentinel
    try:
        ce = float(point.attrib.get("ce", str(COT_UNKNOWN_CE_LE)))
        if ce < COT_UNKNOWN_CE_LE:
            yield "location_fix_accuracy_horizontal_m", enclose_from_float(
                ce, timestamp=ts
            )
    except ValueError:
        pass

    # le: vertical 1-sigma accuracy in metres; skip sentinel
    try:
        le = float(point.attrib.get("le", str(COT_UNKNOWN_CE_LE)))
        if le < COT_UNKNOWN_CE_LE:
            yield "location_fix_accuracy_vertical_m", enclose_from_float(
                le, timestamp=ts
            )
    except ValueError:
        pass

    detail = root.find("detail")
    if detail is None:
        return

    # detail/track: course (degrees true) and speed (m/s -> knots)
    track = detail.find("track")
    if track is not None:
        try:
            course = float(track.attrib["course"])
            yield "course_over_ground_deg", enclose_from_float(course, timestamp=ts)
        except (KeyError, ValueError):
            pass

        try:
            speed_mps = float(track.attrib["speed"])
            speed_knots = speed_mps * 1.94384
            yield "speed_over_ground_knots", enclose_from_float(
                speed_knots, timestamp=ts
            )
        except (KeyError, ValueError):
            pass

    # detail/contact: callsign -> name
    contact = detail.find("contact")
    if contact is not None:
        callsign = contact.attrib.get("callsign", "").strip()
        if callsign:
            yield "name", enclose_from_string(callsign, timestamp=ts)

    # detail/status: battery percentage
    status = detail.find("status")
    if status is not None:
        battery_str = status.attrib.get("battery", "").strip()
        if battery_str:
            try:
                yield "battery_state_of_charge_pct", enclose_from_float(
                    float(battery_str), timestamp=ts
                )
            except ValueError:
                pass


def get_uid_from_xml(xml_bytes: bytes) -> str | None:
    """Extract the uid attribute from a CoT <event> element."""
    try:
        root = ET.fromstring(xml_bytes)
        return root.attrib.get("uid")
    except ET.ParseError:
        return None


def get_stale_from_xml(xml_bytes: bytes) -> float | None:
    """Extract the stale timestamp as a POSIX float from a CoT <event>."""
    try:
        root = ET.fromstring(xml_bytes)
        stale_str = root.attrib.get("stale", "")
        if not stale_str:
            return None
        dt = datetime.strptime(stale_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return dt.timestamp()
    except (ET.ParseError, ValueError):
        return None


def _split_cot_stream(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte buffer into complete CoT event chunks.

    CoT events are XML fragments concatenated with no explicit framing.
    We use the presence of b'</event>' as the end-of-message marker.

    Returns:
        A tuple (complete_events, remainder) where complete_events is a list of
        complete XML byte strings, each ending at </event>, and remainder is any
        trailing incomplete data.
    """
    events = []
    end_tag = b"</event>"
    while True:
        idx = buf.find(end_tag)
        if idx == -1:
            break
        end = idx + len(end_tag)
        events.append(buf[:end].strip())
        buf = buf[end:]
    return events, buf


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


async def _receive_loop(
    reader: asyncio.StreamReader,
    session: zenoh.Session,
    args: argparse.Namespace,
):
    """Read CoT events from the TAK server and publish to Zenoh."""
    buf = b""
    stale_times: dict[str, float] = {}

    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=30.0)
        except asyncio.TimeoutError:
            # Prune stale targets
            now = time.time()
            stale_uids = [uid for uid, stale in stale_times.items() if now > stale]
            for uid in stale_uids:
                logger.debug("Target %s is stale, forgetting", uid)
                del stale_times[uid]
            continue

        if not chunk:
            logger.info("TAK server closed the connection")
            break

        buf += chunk
        events, buf = _split_cot_stream(buf)

        for xml_bytes in events:
            if not xml_bytes:
                continue

            logger.debug("Received CoT event (%d bytes)", len(xml_bytes))
            timestamp = time.time_ns()

            uid = get_uid_from_xml(xml_bytes)
            if not uid:
                logger.debug("CoT event missing uid, skipping")
                continue

            # Check staleness override
            stale_ts = None
            if args.target_timeout_s is not None:
                stale_ts = time.time() + args.target_timeout_s
            else:
                stale_ts = get_stale_from_xml(xml_bytes)

            if stale_ts is not None:
                stale_times[uid] = stale_ts

            # Check if already stale
            if uid in stale_times and time.time() > stale_times[uid]:
                logger.debug("Skipping stale target %s", uid)
                continue

            sanitized = sanitize_uid(uid)
            target_id = f"cot_{sanitized}"

            # Publish raw XML if requested
            if args.publish_raw:
                raw_key = keelson.construct_pubsub_key(
                    args.realm,
                    args.entity_id,
                    "raw",
                    args.source_id,
                    target_id=target_id,
                )
                if raw_key not in PUBLISHERS:
                    PUBLISHERS[raw_key] = session.declare_publisher(raw_key)
                PUBLISHERS[raw_key].put(
                    enclose_from_bytes(xml_bytes, timestamp=timestamp)
                )

            # Parse and publish CoT fields
            for subject, envelope in parse_cot_event(xml_bytes, timestamp=timestamp):
                key = keelson.construct_pubsub_key(
                    args.realm,
                    args.entity_id,
                    subject,
                    args.source_id,
                    target_id=target_id,
                )
                if key not in PUBLISHERS:
                    PUBLISHERS[key] = session.declare_publisher(key)
                PUBLISHERS[key].put(envelope)
                logger.debug("Published subject %s for target %s", subject, target_id)


async def _run_async(session: zenoh.Session, args: argparse.Namespace):
    """Main async loop: connect to TAK server and receive events, with reconnection."""
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
            try:
                await _receive_loop(reader, session, args)
            finally:
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

        logger.info(
            "Disconnected from TAK server. Reconnecting in %.1fs",
            args.reconnect_delay,
        )
        await asyncio.sleep(args.reconnect_delay)


def run(session: zenoh.Session, args: argparse.Namespace):
    """Run the tak2keelson event loop."""
    try:
        asyncio.run(_run_async(session, args))
    except KeyboardInterrupt:
        logger.info("Shutting down tak2keelson")


def main():
    parser = argparse.ArgumentParser(
        prog="tak2keelson",
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
    parser.add_argument("-s", "--source-id", type=str, required=True)

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

    # tak2keelson specific
    parser.add_argument(
        "--publish-raw",
        action="store_true",
        default=False,
        help="Also publish raw CoT XML bytes under the 'raw' subject",
    )
    parser.add_argument(
        "--target-timeout-s",
        type=float,
        default=None,
        help="Hard override for target staleness in seconds. If not set, uses CoT stale field",
    )

    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)

    conf = zenoh.Config()
    if args.mode is not None:
        conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            run(session, args)


if __name__ == "__main__":
    main()
