#!/usr/bin/env python3

"""TAK / CoT server -> Keelson bridge.

Reads CoT XML events from a TAK server over TCP or TLS and republishes the
fields that map onto Keelson subjects under ``@target/cot_{sanitized_uid}``.
"""

import argparse
import asyncio
import json
import logging
import re
import time
import urllib.parse
from configparser import ConfigParser
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytak
import zenoh

import keelson
from keelson.helpers import enclose_from_bytes, enclose_from_float, enclose_from_string
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.scaffolding import declare_liveliness_token

logger = logging.getLogger("tak2keelson")

# CoT uses 9999999.0 as the "value unknown" sentinel for point/@ce and point/@le.
COT_ACCURACY_SENTINEL = 9999999.0

PUBLISHERS: dict[str, zenoh.Publisher] = {}
TARGET_STALE_AT: dict[str, float] = {}

_UID_ALLOWED = re.compile(r"[^a-zA-Z0-9_\-]")

# Exact conversion factor used in the CoT spec (matches pyais/aiscot).
MPS_PER_KNOT = 1.0 / 1.94384


def _sanitize_uid(uid: str) -> str:
    return _UID_ALLOWED.sub("_", uid)


def _mps_to_knots(mps: float) -> float:
    return mps * 1.94384


def _enclose_location_fix(
    lat: float, lon: float, hae: float | None = None, timestamp: int = None
) -> bytes:
    """LocationFix envelope that carries altitude when available.

    ``enclose_from_lon_lat`` in the SDK drops altitude, so we build LocationFix
    directly to preserve CoT ``point/@hae``.
    """
    payload = LocationFix()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.latitude = lat
    payload.longitude = lon
    if hae is not None:
        payload.altitude = hae
    return keelson.enclose(payload.SerializeToString())


def _parse_iso_utc(s: str) -> datetime | None:
    if not s:
        return None
    # Accept both "Z" and offset forms. fromisoformat handles offsets but not "Z".
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_cot_event(
    xml_bytes: bytes, timestamp: int = None, now: datetime | None = None
):
    """Parse a CoT XML event into ``(subject, envelope_bytes)`` pairs.

    Yields only subjects whose source fields are present and not flagged as
    "value unknown" via the 9999999.0 sentinel. Yields nothing at all if the
    event is already past its ``stale`` deadline.
    """
    root = ET.fromstring(xml_bytes)

    stale_attr = root.get("stale")
    stale = _parse_iso_utc(stale_attr) if stale_attr else None
    if stale is not None:
        current = now or datetime.now(timezone.utc)
        if current >= stale:
            return

    point = root.find("point")
    if point is not None:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is not None and lon is not None:
            hae = point.get("hae")
            yield "location_fix", _enclose_location_fix(
                lat=float(lat),
                lon=float(lon),
                hae=float(hae) if hae is not None else None,
                timestamp=timestamp,
            )

        ce = point.get("ce")
        if ce is not None:
            ce_f = float(ce)
            if ce_f != COT_ACCURACY_SENTINEL:
                yield "location_fix_accuracy_horizontal_m", enclose_from_float(
                    ce_f, timestamp=timestamp
                )
        le = point.get("le")
        if le is not None:
            le_f = float(le)
            if le_f != COT_ACCURACY_SENTINEL:
                yield "location_fix_accuracy_vertical_m", enclose_from_float(
                    le_f, timestamp=timestamp
                )

    track = root.find("detail/track")
    if track is not None:
        course = track.get("course")
        if course is not None:
            yield "course_over_ground_deg", enclose_from_float(
                float(course), timestamp=timestamp
            )
        speed = track.get("speed")
        if speed is not None:
            yield "speed_over_ground_knots", enclose_from_float(
                _mps_to_knots(float(speed)), timestamp=timestamp
            )

    contact = root.find("detail/contact")
    if contact is not None:
        callsign = contact.get("callsign")
        if callsign:
            yield "name", enclose_from_string(callsign, timestamp=timestamp)

    status = root.find("detail/status")
    if status is not None:
        battery = status.get("battery")
        if battery is not None:
            yield "battery_state_of_charge_pct", enclose_from_float(
                float(battery), timestamp=timestamp
            )


def _build_pytak_config(args: argparse.Namespace) -> ConfigParser:
    cp = ConfigParser()
    section = {"COT_URL": args.tak_url}
    parsed = urllib.parse.urlparse(args.tak_url)
    if parsed.scheme.startswith("tls") or parsed.scheme == "ssl":
        if args.tak_client_cert:
            section["PYTAK_TLS_CLIENT_CERT"] = args.tak_client_cert
        if args.tak_client_key:
            section["PYTAK_TLS_CLIENT_KEY"] = args.tak_client_key
        if args.tak_ca:
            section["PYTAK_TLS_CLIENT_CAFILE"] = args.tak_ca
        if args.tak_insecure:
            section["PYTAK_TLS_DONT_VERIFY"] = "1"
            section["PYTAK_TLS_DONT_CHECK_HOSTNAME"] = "1"
            logger.warning(
                "--tak-insecure: skipping TLS hostname and certificate verification"
            )
    cp["tak2keelson"] = section
    return cp


def _publish_subjects(
    session: zenoh.Session, args: argparse.Namespace, xml_bytes: bytes
) -> None:
    """Parse one CoT event blob and publish what we can map onto keelson subjects."""
    timestamp = time.time_ns()

    if args.publish_raw:
        if not (pub := PUBLISHERS.get("raw")):
            key = keelson.construct_pubsub_key(
                args.realm, args.entity_id, "raw", args.source_id
            )
            pub = PUBLISHERS["raw"] = session.declare_publisher(key)
        pub.put(enclose_from_bytes(xml_bytes, timestamp=timestamp))

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        logger.exception("Failed to parse CoT event")
        return

    uid = root.get("uid")
    if not uid:
        logger.debug("Event without uid, dropping")
        return
    sanitized = _sanitize_uid(uid)
    target_id = f"cot_{sanitized}"

    # Enforce hard target timeout if one is configured.
    now_mono = time.monotonic()
    if args.target_timeout_s is not None:
        TARGET_STALE_AT[sanitized] = now_mono + args.target_timeout_s

    for subject, envelope in parse_cot_event(xml_bytes, timestamp=timestamp):
        key = keelson.construct_pubsub_key(
            args.realm,
            args.entity_id,
            subject,
            args.source_id,
            target_id=target_id,
        )
        session.put(key, envelope)


class _CoTReceiver(pytak.QueueWorker):
    """Bridge pytak's RX queue into our zenoh publisher."""

    def __init__(self, queue, config, session, args):
        super().__init__(queue, config)
        self._session = session
        self._args = args

    async def handle_data(self, data):
        _publish_subjects(self._session, self._args, data)

    async def run(self, number_of_iterations=-1):
        while True:
            data = await self.queue.get()
            await self.handle_data(data)


async def _run_async(session: zenoh.Session, args: argparse.Namespace) -> None:
    cp = _build_pytak_config(args)
    clitool = pytak.CLITool(cp["tak2keelson"])
    await clitool.setup()
    clitool.add_task(_CoTReceiver(clitool.queue, cp["tak2keelson"], session, args))
    await clitool.run()


def main():
    parser = argparse.ArgumentParser(
        prog="tak2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", type=int, default=logging.INFO)
    parser.add_argument("--mode", "-m", choices=["peer", "client"], type=str)
    parser.add_argument("--connect", action="append", type=str)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, required=True)
    parser.add_argument("--tak-url", type=str, required=True)
    parser.add_argument("--tak-client-cert", type=str, default=None)
    parser.add_argument("--tak-client-key", type=str, default=None)
    parser.add_argument("--tak-ca", type=str, default=None)
    parser.add_argument("--tak-insecure", default=False, action="store_true")
    parser.add_argument("--reconnect-delay", type=float, default=5.0)
    parser.add_argument("--publish-raw", default=False, action="store_true")
    parser.add_argument("--target-timeout-s", type=float, default=None)
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=args.log_level,
    )
    logging.captureWarnings(True)

    conf = zenoh.Config()
    if args.mode is not None:
        conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

    zenoh.init_log_from_env_or(logging.getLevelName(args.log_level))
    with zenoh.open(conf) as session:
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            try:
                asyncio.run(_run_async(session, args))
            except KeyboardInterrupt:
                logger.info("Interrupted, shutting down.")


if __name__ == "__main__":
    main()
