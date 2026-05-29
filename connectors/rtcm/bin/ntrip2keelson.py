#!/usr/bin/env python3
"""
NTRIP caster to Keelson RTCM connector.

This connector:
  1. Authenticates to an NTRIP caster using optional HTTP Basic auth.
  2. Subscribes to Keelson location_fix and converts it to NMEA GGA.
  3. Sends the latest GGA position to the NTRIP caster periodically.
  4. Publishes received RTCM v3 frames to Keelson as raw_rtcm_v3, using the
     same envelope convention as rtcm2keelson.py.
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Optional

import keelson
import zenoh
from keelson.helpers import enclose_from_bytes
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
)
from pyrtcm import RTCMMessageError, RTCMParseError, RTCMReader, RTCMTypeError

LOGGER = logging.getLogger("ntrip2keelson")


@dataclass
class LatestFix:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    timestamp_monotonic: Optional[float] = None
    source: str = "none"


class FixStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fix = LatestFix()

    def update(self, latitude: float, longitude: float, altitude: Optional[float], source: str) -> None:
        if not -90.0 <= latitude <= 90.0:
            raise ValueError(f"invalid latitude: {latitude}")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError(f"invalid longitude: {longitude}")
        with self._lock:
            self._fix = LatestFix(
                latitude=latitude,
                longitude=longitude,
                altitude=altitude,
                timestamp_monotonic=time.monotonic(),
                source=source,
            )

    def get(self) -> LatestFix:
        with self._lock:
            return LatestFix(**self._fix.__dict__)


FIX_STORE = FixStore()
STOP_EVENT = threading.Event()


def nmea_checksum(body: str) -> str:
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def format_lat_lon(latitude: float, longitude: float) -> tuple[str, str, str, str]:
    lat_deg = int(abs(latitude))
    lat_min = (abs(latitude) - lat_deg) * 60.0
    lat_dir = "N" if latitude >= 0 else "S"

    lon_deg = int(abs(longitude))
    lon_min = (abs(longitude) - lon_deg) * 60.0
    lon_dir = "E" if longitude >= 0 else "W"

    return (
        f"{lat_deg:02d}{lat_min:07.4f}",
        lat_dir,
        f"{lon_deg:03d}{lon_min:07.4f}",
        lon_dir,
    )


def make_gga(
    latitude: float,
    longitude: float,
    altitude_m: Optional[float],
    talker_id: str,
    fix_quality: int,
    satellites_used: int,
    hdop: float,
    geoid_separation_m: float,
) -> str:
    """Build a minimal valid NMEA0183 GGA sentence for an NTRIP caster."""
    if len(talker_id) != 2 or not talker_id.isalpha():
        raise ValueError("talker_id must be two alphabetic characters, e.g. GP or GN")

    now = datetime.now(timezone.utc)
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(latitude, longitude)
    altitude = 0.0 if altitude_m is None else altitude_m

    fields = [
        f"{talker_id.upper()}GGA",
        now.strftime("%H%M%S.%f")[:-4],
        lat_str,
        lat_dir,
        lon_str,
        lon_dir,
        str(fix_quality),
        f"{satellites_used:02d}",
        f"{hdop:.1f}",
        f"{altitude:.2f}",
        "M",
        f"{geoid_separation_m:.1f}",
        "M",
        "",
        "",
    ]
    body = ",".join(fields)
    return f"${body}*{nmea_checksum(body)}\r\n"


def getenv_required(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def get_password(args: argparse.Namespace) -> Optional[str]:
    if args.password:
        return args.password
    if args.password_env:
        return os.getenv(args.password_env)
    return None


def build_ntrip_request(args: argparse.Namespace, password: Optional[str]) -> bytes:
    mountpoint = args.mountpoint.lstrip("/")
    http_version = "HTTP/1.1" if args.ntrip_version == "2" else "HTTP/1.0"

    lines = [
        f"GET /{mountpoint} {http_version}",
        f"Host: {args.caster_host}:{args.caster_port}",
        f"User-Agent: {args.user_agent}",
        "Accept: */*",
        "Connection: close",
    ]

    if args.ntrip_version == "2":
        lines.append("Ntrip-Version: Ntrip/2.0")

    if args.username:
        if password is None:
            raise RuntimeError("--username was provided but no password was supplied")
        token = base64.b64encode(f"{args.username}:{password}".encode("utf-8")).decode("ascii")
        lines.append(f"Authorization: Basic {token}")

    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def read_response_headers(stream: BinaryIO) -> str:
    header = bytearray()
    while True:
        line = stream.readline()
        if not line:
            break
        header.extend(line)
        if line in (b"\r\n", b"\n"):
            break
        if len(header) > 65536:
            raise RuntimeError("NTRIP response header exceeded 64 KiB")

    text = header.decode("iso-8859-1", errors="replace")
    first_line = text.splitlines()[0] if text.splitlines() else ""

    accepted = (
        first_line.startswith("ICY 200")
        or first_line.startswith("HTTP/1.0 200")
        or first_line.startswith("HTTP/1.1 200")
    )
    if not accepted:
        raise RuntimeError(f"NTRIP caster rejected connection: {first_line}; headers={text!r}")
    return text


def connect_to_caster(args: argparse.Namespace) -> tuple[socket.socket, BinaryIO]:
    password = get_password(args)
    raw_sock = socket.create_connection(
        (args.caster_host, args.caster_port),
        timeout=args.connect_timeout,
    )
    raw_sock.settimeout(args.socket_timeout)

    if args.tls:
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=args.caster_host)
    else:
        sock = raw_sock

    stream = sock.makefile("rwb", buffering=0)
    stream.write(build_ntrip_request(args, password))
    stream.flush()
    headers = read_response_headers(stream)
    LOGGER.info("Connected to NTRIP caster %s:%s mountpoint=%s", args.caster_host, args.caster_port, args.mountpoint)
    LOGGER.debug("NTRIP response headers:\n%s", headers.strip())
    return sock, stream


def location_fix_callback(sample) -> None:
    try:
        _received_at, _enclosed_at, payload_bytes = keelson.uncover(sample.payload.to_bytes())
        msg = LocationFix.FromString(payload_bytes)
        FIX_STORE.update(msg.latitude, msg.longitude, msg.altitude, "keelson/location_fix")
        LOGGER.debug("Updated position fix lat=%.8f lon=%.8f alt=%.2f", msg.latitude, msg.longitude, msg.altitude)
    except Exception:
        LOGGER.exception("Failed to decode location_fix sample")


def gga_sender_loop(args: argparse.Namespace, stream: BinaryIO) -> None:
    while not STOP_EVENT.wait(args.gga_period):
        fix = FIX_STORE.get()
        if fix.latitude is None or fix.longitude is None:
            LOGGER.warning("No location_fix available yet; not sending GGA")
            continue

        if args.max_fix_age > 0 and fix.timestamp_monotonic is not None:
            age = time.monotonic() - fix.timestamp_monotonic
            if age > args.max_fix_age:
                LOGGER.warning("Latest location_fix is stale: %.1fs > %.1fs; not sending GGA", age, args.max_fix_age)
                continue

        try:
            gga = make_gga(
                fix.latitude,
                fix.longitude,
                fix.altitude,
                args.talker_id,
                args.gga_fix_quality,
                args.gga_satellites_used,
                args.gga_hdop,
                args.gga_geoid_separation,
            )
            stream.write(gga.encode("ascii"))
            stream.flush()
            LOGGER.debug("Sent GGA to caster: %s", gga.strip())
        except Exception:
            LOGGER.exception("Failed to send GGA to NTRIP caster")
            STOP_EVENT.set()
            return


def publish_rtcm_loop(args: argparse.Namespace, publisher, stream: BinaryIO) -> None:
    reader = RTCMReader(stream)
    for raw_data, parsed_data in reader:
        if STOP_EVENT.is_set():
            break
        if raw_data is None:
            continue
        envelope = enclose_from_bytes(raw_data, time.time_ns())
        publisher.put(envelope)
        LOGGER.debug(
            "Published RTCM frame %s (%d bytes)",
            parsed_data.identity if parsed_data else "unknown",
            len(raw_data),
        )


def seed_initial_position(args: argparse.Namespace) -> None:
    if args.initial_latitude is None and args.initial_longitude is None:
        return
    if args.initial_latitude is None or args.initial_longitude is None:
        raise RuntimeError("Both --initial-latitude and --initial-longitude are required when seeding an initial position")
    FIX_STORE.update(args.initial_latitude, args.initial_longitude, args.initial_altitude, "initial-cli")
    LOGGER.info(
        "Seeded initial NTRIP position lat=%.8f lon=%.8f alt=%s",
        args.initial_latitude,
        args.initial_longitude,
        args.initial_altitude,
    )


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntrip2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Subscribe to Keelson position fixes, feed NTRIP GGA, and publish RTCM corrections to Keelson.",
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", required=True, type=str, help="Keelson realm")
    parser.add_argument("-e", "--entity-id", required=True, type=str, help="Keelson entity identifier")
    parser.add_argument("-s", "--source-id", required=True, type=str, help="Source-id for published raw_rtcm_v3 frames")
    parser.add_argument("--position-source-id", default="**", type=str, help="Source-id pattern for subscribed location_fix")

    parser.add_argument("--caster-host", default=os.getenv("NTRIP_CASTER_HOST"), required=os.getenv("NTRIP_CASTER_HOST") is None)
    parser.add_argument("--caster-port", default=int(os.getenv("NTRIP_CASTER_PORT", "2101")), type=int)
    parser.add_argument("--mountpoint", default=os.getenv("NTRIP_MOUNTPOINT"), required=os.getenv("NTRIP_MOUNTPOINT") is None)
    parser.add_argument("--username", default=os.getenv("NTRIP_USERNAME"), type=str, help="NTRIP username; omit for anonymous casters")
    parser.add_argument("--password", default=None, type=str, help="NTRIP password; prefer --password-env or NTRIP_PASSWORD")
    parser.add_argument("--password-env", default="NTRIP_PASSWORD", type=str, help="Environment variable containing NTRIP password")
    parser.add_argument("--ntrip-version", choices=("1", "2"), default=os.getenv("NTRIP_VERSION", "2"))
    parser.add_argument("--tls", action="store_true", default=os.getenv("NTRIP_TLS", "").lower() in ("1", "true", "yes"))
    parser.add_argument("--user-agent", default=os.getenv("NTRIP_USER_AGENT", "NTRIP ntrip2keelson/1.0"))

    parser.add_argument("--talker-id", default=os.getenv("NTRIP_GGA_TALKER_ID", "GP"), type=str)
    parser.add_argument("--gga-period", default=float(os.getenv("NTRIP_GGA_PERIOD", "5.0")), type=positive_float)
    parser.add_argument("--gga-fix-quality", default=int(os.getenv("NTRIP_GGA_FIX_QUALITY", "1")), type=int)
    parser.add_argument("--gga-satellites-used", default=int(os.getenv("NTRIP_GGA_SATELLITES_USED", "12")), type=int)
    parser.add_argument("--gga-hdop", default=float(os.getenv("NTRIP_GGA_HDOP", "1.0")), type=float)
    parser.add_argument("--gga-geoid-separation", default=float(os.getenv("NTRIP_GGA_GEOID_SEPARATION", "0.0")), type=float)
    parser.add_argument("--max-fix-age", default=float(os.getenv("NTRIP_MAX_FIX_AGE", "30.0")), type=float, help="Maximum location_fix age in seconds; <=0 disables stale-checking")

    parser.add_argument("--initial-latitude", default=os.getenv("NTRIP_INITIAL_LATITUDE"), type=float)
    parser.add_argument("--initial-longitude", default=os.getenv("NTRIP_INITIAL_LONGITUDE"), type=float)
    parser.add_argument("--initial-altitude", default=os.getenv("NTRIP_INITIAL_ALTITUDE"), type=float)

    parser.add_argument("--connect-timeout", default=float(os.getenv("NTRIP_CONNECT_TIMEOUT", "10.0")), type=float)
    parser.add_argument("--socket-timeout", default=float(os.getenv("NTRIP_SOCKET_TIMEOUT", "60.0")), type=float)
    parser.add_argument("--reconnect-delay", default=float(os.getenv("NTRIP_RECONNECT_DELAY", "5.0")), type=float)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(level=args.log_level)
    seed_initial_position(args)

    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)
    rtcm_key = keelson.construct_pubsub_key(args.realm, args.entity_id, "raw_rtcm_v3", args.source_id)
    position_key = keelson.construct_pubsub_key(args.realm, args.entity_id, "location_fix", args.position_source_id)

    LOGGER.info("Opening Zenoh session...")
    session = zenoh.open(conf)
    publisher = session.declare_publisher(rtcm_key)
    subscriber = session.declare_subscriber(position_key, location_fix_callback)
    LOGGER.info("Publishing RTCM on: %s", rtcm_key)
    LOGGER.info("Subscribing position from: %s", position_key)

    try:
        with declare_liveliness_token(session, args.realm, args.entity_id, args.source_id):
            with GracefulShutdown() as shutdown:
                while not shutdown.is_requested():
                    STOP_EVENT.clear()
                    sock = None
                    stream = None
                    gga_thread = None
                    try:
                        sock, stream = connect_to_caster(args)
                        gga_thread = threading.Thread(target=gga_sender_loop, args=(args, stream), daemon=True)
                        gga_thread.start()
                        publish_rtcm_loop(args, publisher, stream)
                    except (OSError, RuntimeError, RTCMParseError, RTCMMessageError, RTCMTypeError) as exc:
                        if shutdown.is_requested():
                            break
                        LOGGER.warning("NTRIP/RTCM stream failed: %s", exc)
                    finally:
                        STOP_EVENT.set()
                        if stream is not None:
                            try:
                                stream.close()
                            except Exception:
                                pass
                        if sock is not None:
                            try:
                                sock.close()
                            except Exception:
                                pass
                        if gga_thread is not None:
                            gga_thread.join(timeout=1.0)

                    if not shutdown.is_requested():
                        LOGGER.info("Reconnecting in %.1f seconds", args.reconnect_delay)
                        time.sleep(args.reconnect_delay)
    finally:
        STOP_EVENT.set()
        subscriber.undeclare()
        publisher.undeclare()
        session.close()
        LOGGER.info("Shut down")


if __name__ == "__main__":
    main()
