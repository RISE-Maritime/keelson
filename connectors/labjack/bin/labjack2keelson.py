#!/usr/bin/env python3

"""
Command line utility that reads analog voltage from a LabJack T-series DAQ
(T4/T7/T8) and publishes the values onto the Keelson bus.

Each configured channel reads a named analog input register (e.g. ``AIN0``)
and, where a higher voltage has been attenuated into the device's input range
by an external resistor voltage-divider (or an LJTick-Divider), applies the
inverse scaling so that the *true* voltage is published.

This is a low-rate **polling** connector: every poll cycle reads all channels
in a single LJM call (``eReadNames``) and publishes one sample per channel.
The channel configuration (which terminal, which divider, which Keelson key)
describes the physical wiring of the device, so it is **deployment-static** —
authored in a version-controlled JSON file and loaded once at startup, not
reconfigurable at runtime. If you need high acquisition rates or hardware-
timed simultaneous sampling, that is what LJM stream mode is for and would be
a separate connector.

The native LJM library must be installed on the host for real-device use; the
``--simulate`` mode and ``--help`` work without it.
"""

# pylint: disable=duplicate-code
# pylint: disable=invalid-name

import sys
import math
import time
import json
import logging
import argparse
from pathlib import Path
from collections import namedtuple

import zenoh
from jsonschema import validate, ValidationError
from keelson import (
    construct_pubsub_key,
    enclose,
    is_subject_well_known,
    get_subject_schema,
)
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    put,
    GracefulShutdown,
)

logger = logging.getLogger("labjack")

DEFAULT_SUBJECT = "analog_voltage_v"

# Every channel publishes a scalar voltage, so its subject must map to this
# payload type in the Keelson registry.
EXPECTED_PAYLOAD_TYPE = "keelson.TimestampedFloat"

# Seconds to wait between reconnect attempts after a device read/open failure.
RECONNECT_BACKOFF_S = 5.0

# A resolved channel ready for the read loop: the AIN register to read, the
# precomputed Zenoh key to publish on, and the (scale, offset) that turn a
# measured voltage into the true voltage via ``v_true = v_meas * scale + offset``.
Channel = namedtuple("Channel", ["ain", "key", "scale", "offset"])


# JSON schema for the channel configuration file. Embedded in the binary (no
# separate file to ship or resolve at runtime) — same approach as the
# entity_health connector. See example-config.json for a worked example.
JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "title": "LabJack Voltage Connector",
    "description": (
        "Channel configuration for reading analog voltage from a LabJack "
        "T-series DAQ."
    ),
    "properties": {
        "poll_interval_s": {
            "type": "number",
            "exclusiveMinimum": 0,
            "default": 1.0,
            "description": "Interval in seconds between successive reads of all channels.",
        },
        "channels": {
            "type": "array",
            "minItems": 1,
            "description": "Analog input channels to read and publish.",
            "items": {
                "type": "object",
                "title": "Channel",
                "required": ["ain", "source_id"],
                "properties": {
                    "ain": {
                        "type": "string",
                        "pattern": "^AIN[0-9]+$",
                        "description": "LabJack analog input register name, e.g. AIN0.",
                    },
                    "source_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Keelson source_id for this channel's published "
                            "values. Must be unique across channels."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "default": "analog_voltage_v",
                        "description": (
                            "Keelson subject to publish to. Must map to "
                            "keelson.TimestampedFloat (e.g. analog_voltage_v, "
                            "battery_voltage_v)."
                        ),
                    },
                    "ain_range": {
                        "type": "number",
                        "description": "Optional AINx_RANGE register value (volts, e.g. 10.0).",
                    },
                    "resolution_index": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional AINx_RESOLUTION_INDEX register value (0 = default).",
                    },
                    "settling_us": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Optional AINx_SETTLING_US register value (0 = auto).",
                    },
                    "divider": {
                        "type": "object",
                        "title": "Resistor Divider",
                        "description": (
                            "External voltage divider: R1 in series with the "
                            "signal, R2 from the AIN terminal to ground. True "
                            "voltage = measured * (R1 + R2) / R2."
                        ),
                        "required": ["r1_ohms", "r2_ohms"],
                        "properties": {
                            "r1_ohms": {"type": "number", "exclusiveMinimum": 0},
                            "r2_ohms": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "additionalProperties": False,
                    },
                    "scale": {
                        "type": "number",
                        "description": (
                            "Linear multiplier applied to the measured voltage. "
                            "True voltage = measured * scale + offset."
                        ),
                    },
                    "offset": {
                        "type": "number",
                        "description": "Linear offset (volts) added after scaling.",
                    },
                },
                # divider and scale/offset are mutually exclusive.
                "not": {
                    "allOf": [
                        {"required": ["divider"]},
                        {
                            "anyOf": [
                                {"required": ["scale"]},
                                {"required": ["offset"]},
                            ]
                        },
                    ]
                },
                "additionalProperties": False,
            },
        },
    },
    "required": ["channels"],
    "additionalProperties": False,
}


def _check_unique_source_ids(config: dict) -> None:
    """Reject duplicate per-channel source_ids (clarity / loopback guard)."""
    source_ids = [ch["source_id"] for ch in config.get("channels", [])]
    duplicates = {s for s in source_ids if source_ids.count(s) > 1}
    if duplicates:
        raise ValueError(
            f"Duplicate channel source_id(s) not allowed: {sorted(duplicates)}"
        )


def _check_subjects(config: dict) -> None:
    """Reject channels whose subject is not a known voltage subject.

    The JSON schema can't check this — a typo would otherwise publish to a key
    no consumer expects, silently. Every channel must target a registered
    Keelson subject that carries ``keelson.TimestampedFloat``.
    """
    for ch in config.get("channels", []):
        subject = ch.get("subject", DEFAULT_SUBJECT)
        if not is_subject_well_known(subject):
            raise ValueError(
                f"channel subject {subject!r} is not a known Keelson subject"
            )
        actual = get_subject_schema(subject)
        if actual != EXPECTED_PAYLOAD_TYPE:
            raise ValueError(
                f"channel subject {subject!r} maps to {actual}, "
                f"expected {EXPECTED_PAYLOAD_TYPE}"
            )


def load_config(path: Path) -> dict:
    """Read, schema-validate and sanity-check the channel configuration.

    Raises ``json.JSONDecodeError`` / ``jsonschema.ValidationError`` / ``ValueError``
    on a malformed or inconsistent file; the caller turns those into a clean exit.
    """
    config = json.loads(path.read_text(encoding="UTF-8"))
    validate(config, JSON_SCHEMA)
    _check_unique_source_ids(config)
    _check_subjects(config)
    return config


def resolve_scale_offset(channel: dict) -> tuple[float, float]:
    """Collapse a channel's scaling config into a single ``(scale, offset)``.

    A resistor divider is just a special case of a linear scale, so it is
    normalised here once at load time and the read loop only ever applies
    ``v_true = v_meas * scale + offset``.

    - ``divider``: R1 in series with the signal, R2 from the AIN terminal to
      ground -> ``scale = (R1 + R2) / R2``, ``offset = 0``.
    - ``scale`` / ``offset``: used as-is (also covers LJTick-Divider ratios and
      linear sensor calibration).
    - neither: a direct reading (scale=1, offset=0).

    The schema makes ``divider`` and ``scale``/``offset`` mutually exclusive.
    """
    divider = channel.get("divider")
    if divider is not None:
        r1 = divider["r1_ohms"]
        r2 = divider["r2_ohms"]
        return (r1 + r2) / r2, 0.0
    return channel.get("scale", 1.0), channel.get("offset", 0.0)


def resolve_channels(config: dict, realm: str, entity_id: str) -> list:
    """Build the immutable list of :class:`Channel` to read each cycle."""
    resolved = []
    for ch in config["channels"]:
        scale, offset = resolve_scale_offset(ch)
        key = construct_pubsub_key(
            realm, entity_id, ch.get("subject", DEFAULT_SUBJECT), ch["source_id"]
        )
        resolved.append(Channel(ain=ch["ain"], key=key, scale=scale, offset=offset))
    return resolved


def collect_register_config(config: dict) -> tuple[list, list]:
    """Gather the optional per-channel LJM analog-input register settings into
    a single (names, values) pair, so they can be written in one ``eWriteNames``
    call at startup (and re-applied on every reconnect)."""
    names, values = [], []
    for ch in config["channels"]:
        ain = ch["ain"]
        if "ain_range" in ch:
            names.append(f"{ain}_RANGE")
            values.append(ch["ain_range"])
        if "resolution_index" in ch:
            names.append(f"{ain}_RESOLUTION_INDEX")
            values.append(ch["resolution_index"])
        if "settling_us" in ch:
            names.append(f"{ain}_SETTLING_US")
            values.append(ch["settling_us"])
    return names, values


def _open_device(args: argparse.Namespace):
    """Open the LJM device handle. Imported lazily so the connector runs
    (``--help``, ``--simulate``, tests) without the native LJM library."""
    from labjack import ljm  # noqa: PLC0415

    handle = ljm.openS(args.device_type, args.connection_type, args.identifier)
    info = ljm.getHandleInfo(handle)
    logger.info(
        "Opened LabJack device: type=%s connection=%s serial=%s",
        info[0],
        info[1],
        info[2],
    )
    return ljm, handle


def _open_and_configure(args: argparse.Namespace, reg_names: list, reg_values: list):
    """Open the device and apply the analog-input register configuration."""
    ljm, handle = _open_device(args)
    if reg_names:
        ljm.eWriteNames(handle, len(reg_names), reg_names, reg_values)
        logger.info("Applied %d analog-input register setting(s)", len(reg_names))
    return ljm, handle


def _safe_close(ljm, handle) -> None:
    try:
        ljm.close(handle)
        logger.info("Closed LabJack device")
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        logger.debug("Error closing LabJack handle: %s", exc)


def _reconnect(args, reg_names, reg_values, shutdown):
    """Retry opening + configuring the device until it succeeds or shutdown is
    requested. Returns ``(ljm, handle)``, or ``(None, None)`` if shutdown won."""
    while not shutdown.is_requested():
        try:
            return _open_and_configure(args, reg_names, reg_values)
        except Exception as exc:  # noqa: BLE001 — any open failure -> retry
            logger.warning(
                "LabJack reopen failed (%s); retrying in %.0fs",
                exc,
                RECONNECT_BACKOFF_S,
            )
            shutdown.wait(timeout=RECONNECT_BACKOFF_S)
    return None, None


def _simulated_reading(ain: str, t: float) -> float:
    """Deterministic synthetic AIN voltage for --simulate mode (no hardware).

    Produces a slow sine in the 0-3.3 V range, phase-shifted per channel so
    distinct AINs yield distinct traces.
    """
    # Derive a stable per-channel phase from the channel name.
    phase = sum(ord(c) for c in ain) % 360
    return 1.65 + 1.65 * math.sin(2 * math.pi * (t / 20.0) + math.radians(phase))


def _publish(session, channels, values, timestamp: int) -> None:
    """Scale and publish one reading per channel."""
    for channel, v_meas in zip(channels, values):
        v_true = v_meas * channel.scale + channel.offset
        payload = TimestampedFloat()
        payload.timestamp.FromNanoseconds(timestamp)
        payload.value = v_true
        logger.debug(
            "%s: %.4f V (raw %.4f V) -> %s", channel.ain, v_true, v_meas, channel.key
        )
        put(
            session,
            channel.key,
            enclose(payload.SerializeToString(), enclosed_at=timestamp),
        )


def run(session: zenoh.Session, args: argparse.Namespace, config: dict):
    channels = resolve_channels(config, args.realm, args.entity_id)
    names = [c.ain for c in channels]
    poll_interval_s = config.get("poll_interval_s", 1.0)
    reg_names, reg_values = collect_register_config(config)

    ljm = handle = ljm_error = None
    if args.simulate:
        logger.warning("Running in --simulate mode: no LabJack hardware is used")
    else:
        ljm, handle = _open_and_configure(args, reg_names, reg_values)
        ljm_error = ljm.LJMError

    try:
        with GracefulShutdown() as shutdown:
            while not shutdown.is_requested():
                timestamp = time.time_ns()

                if args.simulate:
                    values = [
                        _simulated_reading(c.ain, timestamp / 1e9) for c in channels
                    ]
                else:
                    try:
                        # One round-trip for every channel: faster than N
                        # eReadName calls and the samples are near-simultaneous.
                        values = ljm.eReadNames(handle, len(names), names)
                    except ljm_error as exc:
                        logger.warning("LabJack read failed (%s); reconnecting...", exc)
                        _safe_close(ljm, handle)
                        ljm, handle = _reconnect(args, reg_names, reg_values, shutdown)
                        if handle is None:
                            break  # shutdown requested mid-reconnect
                        continue

                _publish(session, channels, values, timestamp)

                # Interruptible sleep so SIGINT/SIGTERM take effect promptly.
                shutdown.wait(timeout=poll_interval_s)
    finally:
        if handle is not None:
            _safe_close(ljm, handle)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="labjack2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=(
            "Read analog voltage from a LabJack T-series DAQ and publish it to "
            "Keelson, with per-channel high-voltage divider/scale compensation."
        ),
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)

    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a JSON-encoded channel configuration file.",
    )
    parser.add_argument(
        "--device-type",
        type=str,
        default="ANY",
        help="LJM device type passed to ljm.openS (e.g. T7, T4, T8, ANY).",
    )
    parser.add_argument(
        "--connection-type",
        type=str,
        default="ANY",
        help="LJM connection type passed to ljm.openS (e.g. USB, ETHERNET, ANY).",
    )
    parser.add_argument(
        "--identifier",
        type=str,
        default="ANY",
        help="LJM device identifier passed to ljm.openS (serial/IP/name or ANY).",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Generate synthetic readings without opening a LabJack device.",
    )

    args = parser.parse_args()

    setup_logging(level=args.log_level)

    # Load and validate the JSON config file once; it is deployment-static.
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        logger.error("Config file not found: %s", args.config)
        sys.exit(1)
    except json.JSONDecodeError:
        logger.exception("The provided config file is not valid JSON!")
        sys.exit(1)
    except ValidationError:
        logger.exception(
            "The provided config file does not validate against the JSON schema!"
        )
        sys.exit(1)
    except ValueError:
        logger.exception("The provided config file is invalid!")
        sys.exit(1)

    logger.info("Opening Zenoh session...")
    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    with zenoh.open(zconf) as session:
        # One liveliness token per connector process (this device = one entity).
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.entity_id
        ):
            logger.info("Publishing on:")
            for channel in resolve_channels(config, args.realm, args.entity_id):
                logger.info("  [pub] %s (%s)", channel.key, channel.ain)

            run(session, args, config)
