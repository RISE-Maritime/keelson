#!/usr/bin/env python3

"""
Command line utility that reads analog voltage from a LabJack T-series DAQ
(T4/T7/T8) and publishes the values onto the Keelson bus.

Each configured channel reads a named analog input register (e.g. ``AIN0``)
and, where a higher voltage has been attenuated into the device's input range
by an external resistor voltage-divider (or an LJTick-Divider), applies the
inverse scaling so that the *true* voltage is published.

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
import threading
from pathlib import Path

import zenoh
from jsonschema import validate, ValidationError
from keelson import construct_pubsub_key, construct_rpc_key, enclose
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    GracefulShutdown,
    make_configurable,
)

logger = logging.getLogger("labjack")

DEFAULT_SUBJECT = "analog_voltage_v"


def _find_schema_path() -> Path:
    """Resolve config-schema.json for both dev layout and Docker."""
    candidates = [
        Path(__file__).resolve().parent.parent / "config-schema.json",  # dev
        Path(__file__).resolve().parent / "config-schema.json",  # docker
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError("config-schema.json not found in any expected location")


_SCHEMA_PATH = _find_schema_path()
_SCHEMA = None

# Module-level mutable config protected by a lock (allows set_config from RPC callbacks)
_config: dict = {}
_config_lock = threading.Lock()


def _load_schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="UTF-8"))
    return _SCHEMA


def _check_unique_source_ids(config: dict) -> None:
    """Reject duplicate per-channel source_ids (clarity / loopback guard)."""
    source_ids = [ch["source_id"] for ch in config.get("channels", [])]
    duplicates = {s for s in source_ids if source_ids.count(s) > 1}
    if duplicates:
        raise ValueError(
            f"Duplicate channel source_id(s) not allowed: {sorted(duplicates)}"
        )


def get_config() -> dict:
    with _config_lock:
        return dict(_config)


def set_config(new_config: dict) -> None:
    validate(new_config, _load_schema())
    _check_unique_source_ids(new_config)
    with _config_lock:
        _config.clear()
        _config.update(new_config)
    logger.info("Configuration updated")


def scale_reading(v_meas: float, channel: dict) -> float:
    """Convert a measured AIN voltage to the true voltage for a channel.

    Two mutually-exclusive forms (validated by the JSON schema):

    - ``divider``: external resistor divider, R1 in series with the signal and
      R2 from the AIN terminal to ground. ``v_true = v_meas * (R1 + R2) / R2``.
    - ``scale`` / ``offset``: ``v_true = v_meas * scale + offset``. Also covers
      LJTick-Divider ratios (/4, /5, /10, /25) and linear sensor calibration.

    A channel with neither defaults to a direct reading (scale=1, offset=0).
    """
    divider = channel.get("divider")
    if divider is not None:
        r1 = divider["r1_ohms"]
        r2 = divider["r2_ohms"]
        return v_meas * (r1 + r2) / r2

    scale = channel.get("scale", 1.0)
    offset = channel.get("offset", 0.0)
    return v_meas * scale + offset


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


def _configure_channel_registers(ljm, handle, channel: dict) -> None:
    """Apply optional per-channel LJM analog-input register configuration."""
    ain = channel["ain"]
    names, values = [], []
    if "ain_range" in channel:
        names.append(f"{ain}_RANGE")
        values.append(channel["ain_range"])
    if "resolution_index" in channel:
        names.append(f"{ain}_RESOLUTION_INDEX")
        values.append(channel["resolution_index"])
    if "settling_us" in channel:
        names.append(f"{ain}_SETTLING_US")
        values.append(channel["settling_us"])
    if names:
        ljm.eWriteNames(handle, len(names), names, values)
        logger.debug("Configured %s registers: %s", ain, dict(zip(names, values)))


def _simulated_reading(ain: str, t: float) -> float:
    """Deterministic synthetic AIN voltage for --simulate mode (no hardware).

    Produces a slow sine in the 0-3.3 V range, phase-shifted per channel so
    distinct AINs yield distinct traces.
    """
    # Derive a stable per-channel phase from the channel name.
    phase = sum(ord(c) for c in ain) % 360
    return 1.65 + 1.65 * math.sin(2 * math.pi * (t / 20.0) + math.radians(phase))


def run(session: zenoh.Session, args: argparse.Namespace):
    ljm = handle = None
    if not args.simulate:
        ljm, handle = _open_device(args)
    else:
        logger.warning("Running in --simulate mode: no LabJack hardware is used")

    # Apply per-channel register config once at startup (hardware mode only).
    if not args.simulate:
        for channel in get_config().get("channels", []):
            _configure_channel_registers(ljm, handle, channel)

    # Precompute the Zenoh key for each channel's source_id.
    def key_for(channel: dict) -> str:
        return construct_pubsub_key(
            args.realm,
            args.entity_id,
            channel.get("subject", DEFAULT_SUBJECT),
            channel["source_id"],
        )

    try:
        with GracefulShutdown() as shutdown:
            while not shutdown.is_requested():
                with _config_lock:
                    config = dict(_config)

                timestamp = time.time_ns()

                for channel in config.get("channels", []):
                    ain = channel["ain"]
                    if args.simulate:
                        v_meas = _simulated_reading(ain, timestamp / 1e9)
                    else:
                        v_meas = ljm.eReadName(handle, ain)

                    v_true = scale_reading(v_meas, channel)

                    payload = TimestampedFloat()
                    payload.timestamp.FromNanoseconds(timestamp)
                    payload.value = v_true

                    key = key_for(channel)
                    logger.debug(
                        "%s: %.4f V (raw %.4f V) -> %s", ain, v_true, v_meas, key
                    )
                    session.put(
                        key, enclose(payload.SerializeToString(), enclosed_at=timestamp)
                    )

                time.sleep(config.get("poll_interval_s", 1.0))
    finally:
        if handle is not None:
            ljm.close(handle)
            logger.info("Closed LabJack device")


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

    # Load and validate the JSON config file.
    try:
        initial_config = json.loads(args.config.read_text(encoding="UTF-8"))
        validate(initial_config, _load_schema())
        _check_unique_source_ids(initial_config)
        _config.update(initial_config)
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
            make_configurable(
                session=session,
                base_path=args.realm,
                entity_id=args.entity_id,
                responder_id=args.entity_id,
                get_config_cb=get_config,
                set_config_cb=set_config,
            )

            logger.info("Publishing on:")
            for channel in get_config().get("channels", []):
                _key = construct_pubsub_key(
                    args.realm,
                    args.entity_id,
                    channel.get("subject", DEFAULT_SUBJECT),
                    channel["source_id"],
                )
                logger.info("  [pub] %s (%s)", _key, channel["ain"])
            logger.info("Queryables:")
            logger.info(
                "  [rpc] %s",
                construct_rpc_key(
                    args.realm, args.entity_id, "get_config", args.entity_id
                ),
            )
            logger.info(
                "  [rpc] %s",
                construct_rpc_key(
                    args.realm, args.entity_id, "set_config", args.entity_id
                ),
            )

            run(session, args)
