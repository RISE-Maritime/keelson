#!/usr/bin/env python3

"""Keelson -> TAK / CoT bridge.

Subscribes to the local entity's Keelson subjects and emits CoT XML events
to a TAK server over TCP or TLS.
"""

import argparse
import asyncio
import json
import logging
import urllib.parse
from configparser import ConfigParser
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import pytak
import skarv
import skarv.middlewares
import skarv.utilities
import zenoh
from skarv.utilities.zenoh import mirror

import keelson

logger = logging.getLogger("keelson2tak")

COT_ACCURACY_SENTINEL = 9999999.0

SUBJECTS = [
    "location_fix",
    "location_fix_accuracy_horizontal_m",
    "location_fix_accuracy_vertical_m",
    "course_over_ground_deg",
    "speed_over_ground_knots",
    "name",
]

ARGS: argparse.Namespace = None


def _knots_to_mps(knots: float) -> float:
    return knots / 1.94384


def _resolve_callsign(name_value: str | None, fallback: str | None) -> str | None:
    """Prefer the live ``name`` subject, fall back to the CLI-provided callsign."""
    if name_value:
        return name_value
    return fallback


def _iso_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_cot_event(
    *,
    uid: str,
    cot_type: str,
    how: str,
    stale_seconds: float,
    lat: float,
    lon: float,
    hae: float = 0.0,
    ce: float | None = None,
    le: float | None = None,
    callsign: str | None = None,
    course_deg: float | None = None,
    speed_knots: float | None = None,
    now: datetime | None = None,
) -> bytes:
    """Assemble a CoT ``<event>`` XML document and return its serialized bytes."""
    now = now or datetime.now(timezone.utc)
    stale = now + timedelta(seconds=stale_seconds)

    event = ET.Element(
        "event",
        attrib={
            "version": "2.0",
            "uid": uid,
            "type": cot_type,
            "how": how,
            "time": _iso_utc(now),
            "start": _iso_utc(now),
            "stale": _iso_utc(stale),
        },
    )
    ET.SubElement(
        event,
        "point",
        attrib={
            "lat": f"{lat}",
            "lon": f"{lon}",
            "hae": f"{hae}",
            "ce": f"{ce if ce is not None else COT_ACCURACY_SENTINEL}",
            "le": f"{le if le is not None else COT_ACCURACY_SENTINEL}",
        },
    )

    detail = ET.SubElement(event, "detail")
    if callsign:
        ET.SubElement(detail, "contact", attrib={"callsign": callsign})
    if course_deg is not None or speed_knots is not None:
        track_attrs = {}
        if course_deg is not None:
            track_attrs["course"] = f"{course_deg}"
        if speed_knots is not None:
            track_attrs["speed"] = f"{_knots_to_mps(speed_knots)}"
        ET.SubElement(detail, "track", attrib=track_attrs)

    return ET.tostring(event)


def _unpack(sample):
    _, _, payload = keelson.uncover(sample.value.to_bytes())
    return keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(sample.key_expr)
    )


_TX_QUEUE: asyncio.Queue | None = None
_LOOP: asyncio.AbstractEventLoop | None = None


def _send_cot(xml_bytes: bytes) -> None:
    """Hand a serialized CoT event to the TAK TX queue.

    Called synchronously from the skarv trigger. Schedules a put on the pytak
    TX queue from the asyncio loop. Unit-tested via ``patch.object``.
    """
    if _TX_QUEUE is None or _LOOP is None:
        logger.debug("TAK transport not ready, dropping CoT event")
        return
    asyncio.run_coroutine_threadsafe(_TX_QUEUE.put(xml_bytes), _LOOP)


def _emit_cot() -> None:
    """Skarv trigger body: assemble a CoT event from the vault and send it."""
    location_sample = skarv.get("location_fix")
    if not location_sample:
        logger.debug("No location_fix yet, skipping CoT emission")
        return
    location = _unpack(location_sample)

    course_sample = skarv.get("course_over_ground_deg")
    speed_sample = skarv.get("speed_over_ground_knots")
    name_sample = skarv.get("name")
    ce_sample = skarv.get("location_fix_accuracy_horizontal_m")
    le_sample = skarv.get("location_fix_accuracy_vertical_m")

    name_value = _unpack(name_sample).value if name_sample else None
    callsign = _resolve_callsign(name_value, ARGS.cot_callsign)

    xml = build_cot_event(
        uid=ARGS.cot_uid,
        cot_type=ARGS.cot_type,
        how=ARGS.cot_how,
        stale_seconds=ARGS.cot_stale_seconds,
        lat=location.latitude,
        lon=location.longitude,
        hae=location.altitude,
        ce=_unpack(ce_sample).value if ce_sample else None,
        le=_unpack(le_sample).value if le_sample else None,
        callsign=callsign,
        course_deg=_unpack(course_sample).value if course_sample else None,
        speed_knots=_unpack(speed_sample).value if speed_sample else None,
    )
    _send_cot(xml)


def _build_pytak_config(args: argparse.Namespace) -> ConfigParser:
    cp = ConfigParser()
    cp["keelson2tak"] = _build_pytak_section(args)
    return cp


def _build_pytak_section(args: argparse.Namespace) -> dict:
    """Translate our CLI flags into a pytak config section.

    Connection material comes from either an explicit --tak-url (+ optional
    cert/key/ca) or a TAK data/pref package. For a package, pytak unzips it,
    reads its *.pref (connectString + certificate references) and converts the
    bundled PKCS#12 keystore to PEM, yielding COT_URL + TLS cert/key/CA paths.
    The package holds a private key and passwords — never log the section.
    """
    if args.tak_data_package:
        section = {
            k: v for k, v in pytak.read_pref_package(args.tak_data_package).items() if v
        }
        if args.tak_client_cert or args.tak_client_key or args.tak_ca:
            logger.warning(
                "Ignoring --tak-client-cert/--tak-client-key/--tak-ca: "
                "the data package provides the TLS material"
            )
    else:
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
    return section


async def _run_async(args: argparse.Namespace) -> None:
    global _TX_QUEUE, _LOOP
    _LOOP = asyncio.get_running_loop()

    cp = _build_pytak_config(args)
    clitool = pytak.CLITool(cp["keelson2tak"])
    await clitool.setup()
    _TX_QUEUE = clitool.tx_queue

    # Open the zenoh session, wire mirrors + trigger, then block on pytak tasks.
    conf = zenoh.Config()
    if args.mode is not None:
        conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

    zenoh.init_log_from_env_or(logging.getLevelName(args.log_level))
    with zenoh.open(conf) as session:
        for subject in SUBJECTS:
            mirror(
                session,
                keelson.construct_pubsub_key(
                    args.realm,
                    args.entity_id,
                    subject,
                    getattr(args, f"source_id_{subject}"),
                ),
                subject,
            )
        await clitool.run()


def main():
    global ARGS
    parser = argparse.ArgumentParser(
        prog="keelson2tak",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", type=int, default=logging.INFO)
    parser.add_argument("--mode", "-m", choices=["peer", "client"], type=str)
    parser.add_argument("--connect", action="append", type=str)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    tak_endpoint = parser.add_mutually_exclusive_group(required=True)
    tak_endpoint.add_argument(
        "--tak-url", type=str, help="TAK server URL, e.g. tls://host:8089"
    )
    tak_endpoint.add_argument(
        "--tak-data-package",
        type=str,
        help="Path to a TAK data/pref package (.zip); provides COT_URL + TLS certs",
    )
    parser.add_argument("--tak-client-cert", type=str, default=None)
    parser.add_argument("--tak-client-key", type=str, default=None)
    parser.add_argument("--tak-ca", type=str, default=None)
    parser.add_argument("--tak-insecure", default=False, action="store_true")
    parser.add_argument("--reconnect-delay", type=float, default=5.0)
    parser.add_argument("--cot-uid", type=str, required=True)
    parser.add_argument("--cot-type", type=str, default="a-f-S-X")
    parser.add_argument("--cot-callsign", type=str, default=None)
    parser.add_argument("--cot-how", type=str, default="m-g")
    parser.add_argument("--cot-stale-seconds", type=float, default=60.0)
    parser.add_argument("--emit-at-most-every", type=float, default=1.0)
    parser.add_argument("--emit-period", type=float, default=30.0)
    for subject in SUBJECTS:
        parser.add_argument(f"--source_id_{subject}", type=str, default="**")
    ARGS = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=ARGS.log_level,
    )
    logging.captureWarnings(True)

    skarv.register_middleware(
        "location_fix", skarv.middlewares.throttle(ARGS.emit_at_most_every)
    )
    skarv.trigger("location_fix")(_emit_cot)
    skarv.utilities.call_every(ARGS.emit_period, wait_first=True)(_emit_cot)

    try:
        asyncio.run(_run_async(ARGS))
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down.")


if __name__ == "__main__":
    main()
