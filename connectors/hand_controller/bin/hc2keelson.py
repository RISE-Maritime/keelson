#!/usr/bin/env python3

"""
Command line utility for reading joystick/gamepad controllers and publishing to Keelson/Zenoh.

Reads controller data from Linux joystick device (/dev/input/js0) or TCP relay,
captures axes and button events, and publishes to Keelson subjects on the Zenoh bus.

Supports multiple controller profiles (--controller flag):
- ssrov: Seascape ROV Hand Controller — ROV-function naming with shift modifier
- logitech: Logitech F310/F710 — hardware-descriptive naming, no shift logic

Publishes axes and buttons as TimestampedInt messages. Unmapped buttons
publish to button_pressed/button_released with the button number as value.
"""

import argparse
import sys
import os
import json
import logging
import threading
import time
import struct
import signal
import socket
import select
import traceback
from typing import Dict, Any
from pathlib import Path
from urllib.parse import urlsplit

import yaml
import zenoh
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedInt, TimestampedFloat
from keelson.scaffolding.liveliness import declare_liveliness_token
from keelson.scaffolding.qos_zenoh import declare_publisher


# --- Linux joystick HID event protocol -------------------------------------
# Inlined here (rather than in a sibling module) so this entry-point script
# is the only file that lands in /usr/local/bin/ in the container image —
# avoids the silent-collision risk of flat helper modules with generic names
# (see connectors/hand_controller/CLAUDE.md).

# Linux joystick event types (from linux/joystick.h)
JS_EVENT_BUTTON = 0x01  # Button pressed/released
JS_EVENT_AXIS = 0x02  # Joystick moved
JS_EVENT_INIT = 0x80  # Initial state flag (OR'd with the real type)

# 8-byte event format: timestamp (uint32), value (int16), type (uint8), number (uint8)
_EVENT_FORMAT = "IhBB"
_EVENT_SIZE = 8


def read_event(device_file):
    """Read a single 8-byte joystick event from device_file.

    Returns (timestamp, value, event_type, number), or None on a short read
    or read error. Callers can treat None uniformly as "no event this tick".
    """
    try:
        data = device_file.read(_EVENT_SIZE)
    except OSError:
        return None
    if len(data) < _EVENT_SIZE:
        return None
    return struct.unpack(_EVENT_FORMAT, data)


def normalize_axis(value):
    """Normalize int16 axis value to percent (-100.0..100.0).

    Uses a 32768 divisor for symmetric mapping; clamps as defense in depth.
    """
    return max(-100.0, min(100.0, value * 100.0 / 32768.0))


# Global state
PUBLISHERS: Dict[tuple, Any] = {}  # Cache for lazy publisher creation
shutdown_requested = False  # Flag for graceful shutdown

# Per-axis "last value we actually published" — drives the rate-limit
# suppression check in handle_joystick_event. Also updated by the
# backstop loop so a change-driven event arriving right after a backstop
# tick gets correctly suppressed if redundant.
_axis_last_published: Dict[str, tuple] = {}  # source_id -> (time_ns, value)

# Per-axis canonical "current state" — updated on EVERY axis event we
# observe (including INIT and including events the rate limiter
# suppresses). The backstop loop publishes from this so it always sees
# the freshest value, never a rate-limit-stale one.
_axis_last_known: Dict[str, tuple] = {}  # source_id -> (time_ns, value)

# Shift modifier state — set when the active profile's shift_button is held
_shift_held = False

# Transport-level health flag. True while the input source (USB device or TCP
# relay socket) is currently producing data; False on USB unplug, relay socket
# close, or before first connect. _axis_backstop_tick early-returns while False
# so we stop broadcasting a stale "operator commanded 80 % throttle" the
# moment the controller's gone. Source generators own writes; the backstop
# thread reads. Bool assignment is atomic in CPython, no lock needed.
_source_alive: bool = False

# Search order for built-in profile YAML files (first hit wins)
_PROFILES_DIR_CANDIDATES = [
    Path(os.environ["HC_PROFILES_DIR"]) if os.environ.get("HC_PROFILES_DIR") else None,
    Path(__file__).resolve().parent.parent / "profiles",
    Path("/usr/local/share/hc-profiles"),
]


def _validate_profile(profile: dict) -> dict:
    """Ensure required keys exist; coerce missing optional keys to defaults."""
    required = {"axis_map", "button_name_map"}
    missing = required - profile.keys()
    if missing:
        raise ValueError(f"Profile missing required keys: {sorted(missing)}")
    profile.setdefault("button_to_axis", {})
    profile.setdefault("shift_button", None)
    profile.setdefault("shift_map", {})
    return profile


def load_profile(name_or_path: str) -> dict:
    """Load a controller profile from YAML.

    If `name_or_path` resolves to an existing file, load it directly (custom
    profile). Otherwise treat it as a built-in profile name and search
    `_PROFILES_DIR_CANDIDATES` for `<name>.yaml`.
    """
    p = Path(name_or_path)
    if p.is_file():
        with open(p, encoding="utf-8") as fh:
            return _validate_profile(yaml.safe_load(fh))

    for candidate in _PROFILES_DIR_CANDIDATES:
        if candidate is None:
            continue
        path = candidate / f"{name_or_path}.yaml"
        if path.is_file():
            with open(path, encoding="utf-8") as fh:
                return _validate_profile(yaml.safe_load(fh))

    searched = [str(c) for c in _PROFILES_DIR_CANDIDATES if c is not None]
    raise FileNotFoundError(f"Profile {name_or_path!r} not found (searched {searched})")


logger = logging.getLogger("hc2keelson")


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for container log pipelines."""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info("Shutdown signal received")
    shutdown_requested = True


def get_or_create_publisher(
    session, realm: str, entity_id: str, subject: str, source_id: str
):
    """
    Get or create a Zenoh publisher for the specified subject.

    Publishers are cached globally to avoid recreating them for repeated publishes.
    """
    key = (realm, entity_id, subject, source_id)
    if key not in PUBLISHERS:
        key_expr = keelson.construct_pubsub_key(realm, entity_id, subject, source_id)
        # QoS is derived from the subject (controller inputs resolve to the
        # realtime_control profile — see messages/qos.yaml).
        PUBLISHERS[key] = declare_publisher(session, key_expr)
        logger.debug(f"Created publisher for {key_expr}")
    return PUBLISHERS[key]


def publish_data(
    session, realm: str, entity_id: str, subject: str, value: bytes, source_id: str
):
    """Publish data to a Keelson subject."""
    publisher = get_or_create_publisher(session, realm, entity_id, subject, source_id)
    publisher.put(value)


def enclose_from_int(value: int, timestamp_ns: int | None = None) -> bytes:
    """Create an enclosed TimestampedInt message."""
    if timestamp_ns is None:
        timestamp_ns = time.time_ns()

    payload = TimestampedInt()
    payload.timestamp.FromNanoseconds(timestamp_ns)
    payload.value = value
    return keelson.enclose(payload.SerializeToString())


def enclose_from_float(value: float, timestamp_ns: int | None = None) -> bytes:
    """Create an enclosed TimestampedFloat message."""
    if timestamp_ns is None:
        timestamp_ns = time.time_ns()

    payload = TimestampedFloat()
    payload.timestamp.FromNanoseconds(timestamp_ns)
    payload.value = value
    return keelson.enclose(payload.SerializeToString())


def handle_joystick_event(
    timestamp, value, event_type, number, session, args, profile, source_base
):
    """
    Handle a joystick event and publish to Keelson.

    Source-id pattern: <controller_id>/<input_name>

    - Axes publish as TimestampedFloat in percent (-100.0 to 100.0). INIT-flagged
      axis events (the kernel's bootstrap snapshot on device open) are treated as
      normal events — they become the initial bus state for late joiners.
    - Buttons publish as TimestampedInt (1=pressed, 0=released) to subject
      "button_state_change". INIT-flagged button events are suppressed (the
      operator wasn't actually pressing) and must not mutate shift state either.
    - Digital triggers in button_to_axis publish as TimestampedFloat (0.0 or 100.0)
    - When the profile's shift_button is held, buttons in shift_map publish under
      their shifted name. The shift button itself still publishes its own event.
    """
    global _shift_held
    timestamp_ns = time.time_ns()
    axis_map = profile["axis_map"]
    button_name_map = profile["button_name_map"]
    button_to_axis = profile["button_to_axis"]
    shift_button = profile.get("shift_button")
    shift_map = profile.get("shift_map", {})

    # Capture INIT-flag before stripping it; semantics differ per event type.
    is_init = bool(event_type & JS_EVENT_INIT)
    event_type = event_type & ~JS_EVENT_INIT

    if event_type == JS_EVENT_BUTTON:
        # Drop INIT button events outright: the kernel emits these to communicate
        # current button state on device open, but treating them as real presses
        # would fire spurious commands (e.g. "arm just pressed!") at startup, and
        # mutating _shift_held from a stale snapshot would corrupt the modifier.
        if is_init:
            return

        pressed = value == 1

        # Digital trigger → publish as axis (0.0 or 100.0)
        if number in button_to_axis:
            axis_name = button_to_axis[number]
            axis_value = 100.0 if pressed else 0.0
            source_id = f"{source_base}/{axis_name}"
            _axis_last_known[source_id] = (timestamp_ns, axis_value)
            logger.info(f"Trigger {number} ({axis_name}): {axis_value}")
            publish_data(
                session,
                args.realm,
                args.entity_id,
                axis_name,
                enclose_from_float(axis_value, timestamp_ns),
                source_id,
            )
            _axis_last_published[source_id] = (timestamp_ns, axis_value)
            return

        # Update shift state when the shift button is pressed/released
        # (the shift button still publishes its own event below).
        if shift_button is not None and number == shift_button:
            _shift_held = pressed

        # Resolve button name: shifted variant if shift is held, else normal.
        # Edge case: a release that arrives after shift was released will use
        # the non-shifted name even if the press was shifted. Tolerated for v1.
        if _shift_held and number in shift_map:
            button_name = shift_map[number]
        else:
            button_name = button_name_map.get(number, str(number))
        source_id = f"{source_base}/{button_name}"

        logger.info(
            f"Button {number} ({button_name}) {'pressed' if pressed else 'released'}"
        )
        publish_data(
            session,
            args.realm,
            args.entity_id,
            "button_state_change",
            enclose_from_int(int(pressed), timestamp_ns),
            source_id,
        )

    elif event_type == JS_EVENT_AXIS:
        if number in axis_map:
            axis_name = axis_map[number]
            normalized = normalize_axis(value)
            source_id = f"{source_base}/{axis_name}"

            # Optional center-snap: clean up joystick ADC rest offset by snapping
            # near-zero values to exact 0.0 before the rate-limit check. Without
            # this, a released stick publishes its residual offset (e.g. -0.39 %)
            # and the deadband then freezes that residual in place.
            if (
                args.axis_center_snap_pct > 0
                and abs(normalized) < args.axis_center_snap_pct
            ):
                normalized = 0.0

            now_ns = time.time_ns()

            # Update canonical state on every event, even ones the rate limiter
            # is about to suppress — the backstop loop reads from here, not from
            # _axis_last_published, so it always sees the freshest value.
            _axis_last_known[source_id] = (now_ns, normalized)

            # Rate-limit per axis: skip publishing if value barely changed AND
            # interval too short. axis_max_hz == 0 disables the cap.
            if args.axis_max_hz > 0:
                prev = _axis_last_published.get(source_id)
                if prev is not None:
                    prev_time, prev_val = prev
                    dt_ns = now_ns - prev_time
                    dv = abs(normalized - prev_val)
                    min_interval_ns = int(1_000_000_000 / args.axis_max_hz)
                    if dt_ns < min_interval_ns and dv < args.axis_deadband_pct:
                        return  # skip -- too soon and value barely moved

            _axis_last_published[source_id] = (now_ns, normalized)
            logger.debug(f"Axis {number} ({axis_name}): {value} -> {normalized:.3f}")

            publish_data(
                session,
                args.realm,
                args.entity_id,
                axis_name,
                enclose_from_float(normalized, now_ns),
                source_id,
            )
        else:
            logger.debug(f"Ignoring unmapped axis {number}: {value}")


def event_source_device(device_path):
    """
    Generator that yields joystick events from a Linux device file.

    Opens /dev/input/jsX and reads 8-byte HID events. Sets _source_alive
    True after a successful open and clears it when the device file
    vanishes (USB unplug) — the backstop reads that flag to decide whether
    to republish last-known axis values. We don't try to re-attach a
    vanished device: the kernel may renumber it (js0 → js1), so recovery
    is a process restart, handled by the operator's supervisor.
    """
    global _source_alive
    path = Path(device_path)
    if not path.exists():
        logger.error(f"Joystick device {device_path} not found!")
        logger.error("Available devices:")
        input_dir = Path("/dev/input")
        if input_dir.exists():
            for dev in sorted(input_dir.glob("js*")):
                logger.error(f"  {dev}")
        sys.exit(1)

    logger.info(f"Opening joystick device {device_path}...")
    try:
        js_device = open(device_path, "rb", buffering=0)
        logger.info(f"Opened joystick device {device_path}")
    except IOError as e:
        logger.error(f"Failed to open joystick device {device_path}: {e}")
        logger.error("Make sure you have permissions to read the device")
        logger.error("Try: sudo chmod a+r /dev/input/js*")
        sys.exit(1)

    _source_alive = True
    try:
        while not shutdown_requested:
            # Periodic path-existence check catches USB unplug. The kernel
            # doesn't notify us; without this we'd keep marking the fd
            # readable (EOF on read) and spin until shutdown.
            if _source_alive and not path.exists():
                logger.warning(
                    f"Joystick device {device_path} vanished — transport dead"
                )
                _source_alive = False

            if not _source_alive:
                # Don't spin on a dead fd. shutdown_requested check at the
                # top of the next iter still lets SIGTERM through promptly.
                time.sleep(0.1)
                continue

            # Wait for data on the device fd instead of busy-polling with sleep
            ready, _, _ = select.select([js_device], [], [], 0.01)
            if ready:
                event = read_event(js_device)
                if event:
                    yield event
    finally:
        _source_alive = False
        js_device.close()
        logger.info("Joystick device closed")


def event_source_tcp(host: str, port: int, max_retries: int = 0):
    """
    Generator that yields joystick events from a TCP relay.

    Connects to a host-side relay that reads the joystick and forwards
    8-byte events over TCP. Reconnects automatically on disconnect with
    exponential backoff (1s, 2s, 4s, ..., capped at 30s). Sets
    _source_alive True while connected and clears it during retries —
    the backstop reads that flag to stop republishing last-known axis
    values once the relay's gone. The relay process exits on
    JOYDEVICEREMOVED, so a host-side USB unplug propagates here as a
    socket close.

    max_retries=0 means unlimited (default); a positive value exits the
    generator after that many consecutive connect failures.

    To prevent buffer bloat during fast movement, all buffered events are
    parsed at once and only the LATEST event per (type, number) is yielded.
    This deduplicates axis values so subscribers always see the most recent state.
    """
    global _source_alive
    buf = b""
    retry_count = 0
    backoff_seconds = 1.0
    max_backoff = 30.0

    while not shutdown_requested:
        sock = None
        try:
            logger.info(f"Connecting to relay at {host}:{port}...")
            sock = socket.create_connection((host, port), timeout=1.0)
            sock.settimeout(1.0)
            logger.info(f"Connected to relay at {host}:{port}")
            _source_alive = True
            retry_count = 0
            backoff_seconds = 1.0  # reset on successful connect

            while not shutdown_requested:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    logger.warning("Relay disconnected")
                    _source_alive = False
                    break

                buf += data

                # Parse ALL buffered events, keep only latest per (type, number)
                latest = (
                    {}
                )  # (event_type, number) -> (timestamp, value, event_type, number)
                button_events = []  # buttons are order-sensitive, keep all
                while len(buf) >= 8:
                    timestamp, value, event_type, number = struct.unpack(
                        "IhBB", buf[:8]
                    )
                    buf = buf[8:]
                    raw_type = event_type & ~0x80  # strip INIT flag
                    if raw_type == 0:
                        continue  # pure-INIT or unknown event type, nothing to dispatch
                    if (
                        raw_type == 0x01
                    ):  # JS_EVENT_BUTTON -- keep all (press/release order matters)
                        button_events.append((timestamp, value, event_type, number))
                    else:  # JS_EVENT_AXIS -- only keep latest per axis number
                        latest[(event_type, number)] = (
                            timestamp,
                            value,
                            event_type,
                            number,
                        )

                # Yield button events first (order matters)
                yield from button_events
                # Yield only the latest value per axis
                yield from latest.values()

        except (ConnectionRefusedError, OSError) as e:
            _source_alive = False
            retry_count += 1
            if 0 < max_retries <= retry_count:
                logger.error(
                    f"Relay unreachable after {retry_count} attempts; giving up."
                )
                return
            logger.warning(
                f"Cannot connect to relay: {e}. "
                f"Retry #{retry_count} in {backoff_seconds:.1f}s..."
            )
        finally:
            # Belt-and-braces: any path out of the connection try-block
            # leaves the source stale, whether the explicit clear above ran
            # or not. The finally runs once per outer-loop iteration; the
            # yields inside don't trip it.
            _source_alive = False
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

        if not shutdown_requested:
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, max_backoff)


def _axis_backstop_tick(session, args, now_ns):
    """Republish the last-known value of every observed axis.

    Called by the backstop thread at args.axis_min_hz. Reads from
    _axis_last_known (the canonical state dict, updated on every observed
    axis event including rate-limited ones) so it always sees the freshest
    value. Updates _axis_last_published so a change-driven publish arriving
    just after a backstop tick gets correctly suppressed if redundant.

    Early-returns while _source_alive is False (transport-level dead-man),
    so the connector stops broadcasting the last stick position the moment
    the controller's gone. Recovery is automatic: source reconnect → INIT
    burst refreshes _axis_last_known → flag flips True → backstop resumes.

    source_id encodes "<base>/<subject>"; we split to recover the subject.
    """
    if not _source_alive:
        return

    # Snapshot to avoid mutation during iteration. dict.items() over a live
    # dict from another thread is unsafe even in CPython under contention.
    for source_id, (_, value) in list(_axis_last_known.items()):
        try:
            _, subject = source_id.split("/", 1)
        except ValueError:
            logger.warning(f"backstop: malformed source_id {source_id!r}; skipping")
            continue
        try:
            publish_data(
                session,
                args.realm,
                args.entity_id,
                subject,
                enclose_from_float(value, now_ns),
                source_id,
            )
            _axis_last_published[source_id] = (now_ns, value)
        except Exception:
            logger.exception(f"backstop publish failed for {source_id}")


def _axis_backstop_loop(session, args, stop_event):
    """Daemon thread driving _axis_backstop_tick at args.axis_min_hz.

    Note: this loop does NOT consult the rate cap (args.axis_max_hz). The
    cap exists to suppress redundant *change-driven* publishes; the backstop
    is the floor and must always fire. If a user pushes axis_min_hz above
    axis_max_hz it would invert that contract — terminal_inputs() rejects
    that combination at parse time.
    """
    interval = 1.0 / args.axis_min_hz
    while not stop_event.wait(interval):
        if shutdown_requested:
            return
        _axis_backstop_tick(session, args, time.time_ns())


def terminal_inputs():
    """Parse the terminal inputs and return the arguments."""
    parser = argparse.ArgumentParser(
        prog="keelson_connector_hc",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Read joystick/gamepad controller and publish to Keelson/Zenoh",
    )

    parser.add_argument(
        "-l",
        "--log-level",
        type=int,
        default=20,
        help="Log level 10=DEBUG, 20=INFO, 30=WARN, 40=ERROR, 50=CRITICAL 0=NOTSET",
    )
    parser.add_argument(
        "--mode",
        "-m",
        dest="mode",
        choices=["peer", "client"],
        type=str,
        help="The zenoh session mode.",
    )
    parser.add_argument(
        "--connect",
        action="append",
        type=str,
        help="Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447",
    )
    parser.add_argument(
        "-r",
        "--realm",
        default="rise",
        type=str,
        help="Unique id for a domain/realm to connect ex. rise",
    )
    parser.add_argument(
        "-e",
        "--entity-id",
        default="rov",
        type=str,
        help="Entity being a unique id representing an entity within the realm",
    )
    parser.add_argument(
        "--device",
        "-d",
        type=str,
        default="/dev/input/js0",
        help="Joystick device path (default: /dev/input/js0)",
    )
    parser.add_argument(
        "--relay",
        type=str,
        default=None,
        help="TCP relay address (host:port) for cross-platform mode. "
        "Reads joystick events from a TCP relay instead of a device file. "
        "Example: --relay host.docker.internal:9090",
    )
    parser.add_argument(
        "-c",
        "--controller",
        type=str,
        default="ssrov",
        help="Controller profile name (resolves to profiles/<name>.yaml).",
    )
    parser.add_argument(
        "--controller-config",
        type=str,
        default=None,
        help="Path to a custom controller-profile YAML file. Overrides --controller.",
    )
    parser.add_argument(
        "--relay-max-retries",
        type=int,
        default=0,
        help="Max relay connection attempts before exit (0 = unlimited).",
    )
    # --- Per-axis publish-rate bounds (paired). Both in Hz. ----------------
    # Lower bound = backstop republish floor; upper bound = rate cap on
    # change-driven events when the value moved less than the deadband.
    parser.add_argument(
        "--axis-min-hz",
        type=float,
        default=10.0,
        help=(
            "Lower bound on axis publish rate. A background loop republishes "
            "the last-known value of every observed axis at this minimum rate "
            "so subscribers recover from packet loss and bootstrap late joiners. "
            "0 disables the backstop (pure change-driven)."
        ),
    )
    parser.add_argument(
        "--axis-max-hz",
        type=float,
        default=50.0,
        help=(
            "Upper bound on axis publish rate. Suppresses a change-driven "
            "publish if the previous publish for that axis was less than "
            "1/N seconds ago AND the value moved by less than "
            "--axis-deadband-pct. 0 disables the rate cap."
        ),
    )
    parser.add_argument(
        "--axis-deadband-pct",
        type=float,
        default=1.0,
        help=(
            "Percentage-point change that always publishes immediately, "
            "bypassing --axis-max-hz. Independent of --axis-min-hz / --axis-max-hz."
        ),
    )
    parser.add_argument(
        "--axis-center-snap-pct",
        type=float,
        default=0.0,
        help=(
            "Snap |value| < this to 0.0 before the rate-limit check. "
            "Cleans up joystick ADC rest offset so a released stick publishes exactly 0.0. "
            "0 disables; recommended 2.0. Loses sub-snap precision near rest."
        ),
    )
    parser.add_argument(
        "--log-json",
        action="store_true",
        help="Emit logs as one JSON object per line (for container log pipelines).",
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Override the source-id base. Defaults to the --controller value. "
        "Use this to run two of the same controller side-by-side with distinct "
        "source-id prefixes (e.g. --controller ssrov --source-id ssrov-port).",
    )

    args = parser.parse_args()

    # Cross-flag validation: when both bounds are active, min must not exceed max.
    if (
        args.axis_min_hz > 0
        and args.axis_max_hz > 0
        and args.axis_min_hz > args.axis_max_hz
    ):
        parser.error(
            f"--axis-min-hz ({args.axis_min_hz}) must be <= "
            f"--axis-max-hz ({args.axis_max_hz})"
        )

    return args


def main():
    # Parse arguments
    args = terminal_inputs()

    # Setup logging — JSON or human-readable
    handler = logging.StreamHandler()
    if args.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    logging.root.handlers = [handler]
    logging.root.setLevel(args.log_level)
    logging.captureWarnings(True)

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Configure Zenoh
    conf = zenoh.Config()
    if args.mode is not None:
        conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

    # Initialize Zenoh logging
    zenoh.init_log_from_env_or(logging.getLevelName(args.log_level))

    # Select event source
    if args.relay:
        try:
            parsed = urlsplit(f"//{args.relay}")
            relay_host = parsed.hostname
            relay_port = parsed.port
        except ValueError:
            relay_host = None
            relay_port = None
        if not relay_host or relay_port is None:
            logger.error(f"Invalid relay address: {args.relay} (expected host:port)")
            sys.exit(1)
        source = event_source_tcp(relay_host, relay_port, args.relay_max_retries)
        source_desc = f"TCP relay {args.relay}"
    else:
        source = event_source_device(args.device)
        source_desc = f"device {args.device}"

    # Load controller profile (custom path takes precedence over built-in name)
    profile_ref = args.controller_config or args.controller
    try:
        profile = load_profile(profile_ref)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Failed to load controller profile {profile_ref!r}: {e}")
        sys.exit(1)
    source_base = args.source_id or args.controller
    logger.info(f"Controller profile: {profile_ref} (source-base: {source_base})")

    logger.info("Opening Zenoh session...")
    with (
        zenoh.open(conf) as session,
        declare_liveliness_token(session, args.realm, args.entity_id, source_base),
    ):
        logger.info(f"Connected to realm: {args.realm}, entity: {args.entity_id}")
        logger.info(f"Source base: {source_base}")
        logger.info("Declared liveliness token (controller alive)")
        logger.info(f"Reading controller events from {source_desc}...")
        axis_subjects = sorted(
            set(profile["axis_map"].values()) | set(profile["button_to_axis"].values())
        )
        button_names = sorted(profile["button_name_map"].values())
        logger.info(f"Axis subjects (TimestampedFloat): {', '.join(axis_subjects)}")
        logger.info(f"Button names: {', '.join(button_names)}")
        logger.info(f"Key pattern: {{subject}}/{source_base}/{{function}}")

        # Periodic backstop: republishes the last-known value of every
        # observed axis at args.axis_min_hz so subscribers recover from
        # packet loss and bootstrap late joiners. 0 disables.
        backstop_stop = threading.Event()
        backstop_thread = None
        if args.axis_min_hz > 0:
            backstop_thread = threading.Thread(
                target=_axis_backstop_loop,
                args=(session, args, backstop_stop),
                name="hc-axis-backstop",
                daemon=True,
            )
            backstop_thread.start()
            logger.info(f"Axis backstop running at {args.axis_min_hz} Hz")
        else:
            logger.info("Axis backstop disabled (--axis-min-hz 0)")

        event_count = 0

        try:
            for timestamp, value, event_type, number in source:
                if shutdown_requested:
                    break
                event_count += 1
                handle_joystick_event(
                    timestamp,
                    value,
                    event_type,
                    number,
                    session,
                    args,
                    profile,
                    source_base,
                )

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Error: {e}")
            logger.error(traceback.format_exc())
        finally:
            backstop_stop.set()
            if backstop_thread is not None:
                backstop_thread.join(timeout=2.0)
            logger.info(f"Processed {event_count} events")


if __name__ == "__main__":
    main()
