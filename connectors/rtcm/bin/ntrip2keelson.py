#!/usr/bin/env python3
"""Authenticated NTRIP client that publishes RTCM v3 corrections to Keelson."""

from __future__ import annotations

import argparse
import base64
import logging
import os
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Optional

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

logger = logging.getLogger("ntrip2keelson")


class LatestFix:
    """Mutable holder for the most recent rover position fix."""

    def __init__(self) -> None:
        self.latitude: Optional[float] = None
        self.longitude: Optional[float] = None
        self.altitude: Optional[float] = None
        self.timestamp_ns: Optional[int] = None

    def update(
        self,
        latitude: float,
        longitude: float,
        altitude: Optional[float],
        timestamp_ns: Optional[int] = None,
    ) -> None:
        """Update the stored position."""
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.timestamp_ns = timestamp_ns

    def snapshot(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Return the current latitude, longitude, and altitude."""
        return self.latitude, self.longitude, self.altitude


_latest_fix = LatestFix()
_latest_fix_lock = threading.Lock()
_shutdown: Optional[GracefulShutdown] = None


def nmea_checksum(sentence_without_dollar: str) -> str:
    """Return the NMEA 0183 XOR checksum as two uppercase hex characters."""
    checksum = 0
    for char in sentence_without_dollar:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def format_lat_lon(latitude: float, longitude: float) -> tuple[str, str, str, str]:
    """Convert decimal degrees to NMEA latitude/longitude fields."""
    lat_deg = int(abs(latitude))
    lat_min = (abs(latitude) - lat_deg) * 60.0
    lat_dir = "N" if latitude >= 0.0 else "S"

    lon_deg = int(abs(longitude))
    lon_min = (abs(longitude) - lon_deg) * 60.0
    lon_dir = "E" if longitude >= 0.0 else "W"

    return (
        f"{lat_deg:02d}{lat_min:07.4f}",
        lat_dir,
        f"{lon_deg:03d}{lon_min:07.4f}",
        lon_dir,
    )


def make_gga(
    latitude: float,
    longitude: float,
    altitude_m: Optional[float] = None,
    talker_id: str = "GP",
) -> str:
    """Create a minimal NMEA GGA sentence for NTRIP caster position feedback."""
    now = datetime.now(timezone.utc)
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(latitude, longitude)
    altitude = 0.0 if altitude_m is None else altitude_m

    fields = [
        f"{talker_id}GGA",
        now.strftime("%H%M%S.%f")[:-4],
        lat_str,
        lat_dir,
        lon_str,
        lon_dir,
        "1",
        "12",
        "1.0",
        f"{altitude:.2f}",
        "M",
        "0.0",
        "M",
        "",
        "",
    ]
    body = ",".join(fields)
    return f"${body}*{nmea_checksum(body)}\r\n"


def build_ntrip_request(args: argparse.Namespace, password: str) -> bytes:
    """Build an authenticated NTRIP HTTP request."""
    mountpoint = args.mountpoint.lstrip("/")
    credentials = f"{args.username}:{password}".encode("utf-8")
    auth_value = base64.b64encode(credentials).decode("ascii")
    http_version = "HTTP/1.1" if args.ntrip_version == "2" else "HTTP/1.0"

    lines = [
        f"GET /{mountpoint} {http_version}",
        f"Host: {args.caster_host}:{args.caster_port}",
        f"User-Agent: {args.user_agent}",
        "Accept: */*",
        "Connection: close",
        f"Authorization: Basic {auth_value}",
    ]

    if args.ntrip_version == "2":
        lines.append("Ntrip-Version: Ntrip/2.0")

    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def read_response_headers(stream) -> str:
    """Read and validate NTRIP response headers from a byte stream."""
    header_bytes = bytearray()

    while True:
        line = stream.readline()
        if not line:
            break

        header_bytes.extend(line)
        if line in (b"\r\n", b"\n"):
            break
        if len(header_bytes) > 65_536:
            raise RuntimeError("NTRIP response header too large")

    headers = header_bytes.decode("iso-8859-1", errors="replace")
    first_line = headers.splitlines()[0] if headers.splitlines() else ""
    accepted = (
        first_line.startswith("ICY 200")
        or first_line.startswith("HTTP/1.0 200")
        or first_line.startswith("HTTP/1.1 200")
    )

    if not accepted:
        raise RuntimeError(f"NTRIP caster rejected connection: {first_line}\n{headers}")

    return headers


def resolve_password(args: argparse.Namespace) -> str:
    """Resolve the NTRIP password from an argument or environment variable."""
    if args.password:
        return args.password

    password = os.environ.get(args.password_env)
    if password:
        return password

    raise RuntimeError(
        f"Provide --password or set environment variable {args.password_env}"
    )


def timestamp_from_location_fix(fix: LocationFix) -> Optional[int]:
    """Return a nanosecond timestamp from a Foxglove LocationFix if present."""
    if not fix.timestamp.seconds and not fix.timestamp.nanos:
        return None
    return fix.timestamp.seconds * 1_000_000_000 + fix.timestamp.nanos


def update_latest_fix_from_sample(sample) -> None:
    """Decode a Keelson location_fix sample and update the latest rover position."""
    try:
        _received_at, _enclosed_at, payload = keelson.uncover(sample.payload.to_bytes())
        fix = LocationFix.FromString(payload)

        if not (-90.0 <= fix.latitude <= 90.0):
            logger.debug("Ignoring invalid latitude: %s", fix.latitude)
            return
        if not (-180.0 <= fix.longitude <= 180.0):
            logger.debug("Ignoring invalid longitude: %s", fix.longitude)
            return

        timestamp_ns = timestamp_from_location_fix(fix)
        with _latest_fix_lock:
            _latest_fix.update(
                fix.latitude,
                fix.longitude,
                fix.altitude,
                timestamp_ns,
            )

        logger.debug(
            "Updated latest fix: lat=%.8f lon=%.8f alt=%.2f",
            fix.latitude,
            fix.longitude,
            fix.altitude,
        )
    except Exception:
        logger.exception("Failed to decode location_fix sample")


def connect_to_caster(args: argparse.Namespace, password: str):
    """Connect to the NTRIP caster and return the socket plus readable stream."""
    logger.info(
        "Connecting to NTRIP caster %s:%d mountpoint=%s",
        args.caster_host,
        args.caster_port,
        args.mountpoint,
    )

    raw_sock = socket.create_connection(
        (args.caster_host, args.caster_port),
        timeout=args.connect_timeout,
    )
    raw_sock.settimeout(args.socket_timeout)

    sock = raw_sock
    if args.tls:
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=args.caster_host)

    sock.sendall(build_ntrip_request(args, password))
    stream = sock.makefile("rb", buffering=0)
    headers = read_response_headers(stream)

    logger.info("NTRIP caster accepted connection")
    logger.debug("NTRIP response headers:\n%s", headers.strip())
    return sock, stream


def gga_sender_loop(sock: socket.socket, args: argparse.Namespace) -> None:
    """Periodically send the latest rover position to the NTRIP caster."""
    last_sent = 0.0

    while _shutdown is None or not _shutdown.is_requested():
        now = time.monotonic()
        if now - last_sent < args.gga_period:
            time.sleep(0.1)
            continue

        with _latest_fix_lock:
            latitude, longitude, altitude = _latest_fix.snapshot()

        if latitude is None or longitude is None:
            logger.debug("No location_fix available yet; not sending GGA")
            time.sleep(0.5)
            continue

        try:
            gga = make_gga(latitude, longitude, altitude, args.talker_id)
            sock.sendall(gga.encode("ascii"))
            last_sent = now
            logger.debug("Sent GGA to caster: %s", gga.strip())
        except OSError as exc:
            logger.warning("Failed to send GGA to caster: %s", exc)
            if _shutdown is not None:
                _shutdown.request()
            return


def seed_initial_position(args: argparse.Namespace) -> None:
    """Seed the latest fix from optional command-line coordinates."""
    if args.initial_latitude is None or args.initial_longitude is None:
        return

    with _latest_fix_lock:
        _latest_fix.update(
            args.initial_latitude,
            args.initial_longitude,
            args.initial_altitude,
            time.time_ns(),
        )

    logger.info(
        "Seeded initial NTRIP position lat=%.8f lon=%.8f alt=%s",
        args.initial_latitude,
        args.initial_longitude,
        args.initial_altitude,
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ntrip2keelson command-line arguments."""
    add_common_arguments(parser)
    parser.add_argument("-r", "--realm", required=True, type=str)
    parser.add_argument("-e", "--entity-id", required=True, type=str)
    parser.add_argument("-s", "--source-id", required=True, type=str)
    parser.add_argument("--caster-host", required=True, type=str)
    parser.add_argument("--caster-port", type=int, default=2101)
    parser.add_argument("--mountpoint", required=True, type=str)
    parser.add_argument("--username", required=True, type=str)
    parser.add_argument("--password", type=str)
    parser.add_argument("--password-env", type=str, default="NTRIP_PASSWORD")
    parser.add_argument("--position-source-id", type=str, default="**")
    parser.add_argument("--initial-latitude", type=float)
    parser.add_argument("--initial-longitude", type=float)
    parser.add_argument("--initial-altitude", type=float)
    parser.add_argument("--talker-id", type=str, default="GP")
    parser.add_argument("--gga-period", type=float, default=5.0)
    parser.add_argument("--ntrip-version", choices=["1", "2"], default="2")
    parser.add_argument("--user-agent", type=str, default="NTRIP ntrip2keelson/0.1")
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--socket-timeout", type=float, default=30.0)
    parser.add_argument("--reconnect-delay", type=float, default=5.0)
    parser.add_argument("--tls", action="store_true")


def main() -> None:
    """Run the ntrip2keelson connector."""
    global _shutdown

    parser = argparse.ArgumentParser(
        prog="ntrip2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Authenticated NTRIP caster to Keelson RTCM connector",
    )
    add_arguments(parser)
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    seed_initial_position(args)
    password = resolve_password(args)

    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    rtcm_key = keelson.construct_pubsub_key(
        args.realm,
        args.entity_id,
        "raw_rtcm_v3",
        args.source_id,
    )
    position_key = keelson.construct_pubsub_key(
        args.realm,
        args.entity_id,
        "location_fix",
        args.position_source_id,
    )

    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)
    publisher = session.declare_publisher(rtcm_key)
    subscriber = session.declare_subscriber(position_key, update_latest_fix_from_sample)

    logger.info("Publishing RTCM on: %s", rtcm_key)
    logger.info("Subscribing position from: %s", position_key)

    with declare_liveliness_token(
        session,
        args.realm,
        args.entity_id,
        args.source_id,
    ):
        with GracefulShutdown() as shutdown:
            _shutdown = shutdown

            while not shutdown.is_requested():
                sock = None
                stream = None

                try:
                    sock, stream = connect_to_caster(args, password)
                    gga_thread = threading.Thread(
                        target=gga_sender_loop,
                        args=(sock, args),
                        daemon=True,
                    )
                    gga_thread.start()

                    reader = RTCMReader(stream)
                    for raw_data, parsed_data in reader:
                        if shutdown.is_requested():
                            break
                        if raw_data is None:
                            continue

                        publisher.put(enclose_from_bytes(raw_data, time.time_ns()))
                        logger.debug(
                            "Published RTCM frame: %s (%d bytes)",
                            parsed_data.identity if parsed_data else "unknown",
                            len(raw_data),
                        )

                except (
                    OSError,
                    RuntimeError,
                    RTCMParseError,
                    RTCMMessageError,
                    RTCMTypeError,
                ) as exc:
                    if shutdown.is_requested():
                        break
                    logger.warning("NTRIP/RTCM stream failed: %s", exc)
                    logger.info("Reconnecting in %.1f seconds", args.reconnect_delay)
                    time.sleep(args.reconnect_delay)
                finally:
                    if stream is not None:
                        stream.close()
                    if sock is not None:
                        sock.close()

    subscriber.undeclare()
    publisher.undeclare()
    session.close()
    logger.info("Shut down.")


if __name__ == "__main__":
    main()
