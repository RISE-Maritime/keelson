#!/usr/bin/env python3

"""Key an autopilot for MAVLink 2 signing: once, at install, over a local link (crowsnest-dev#314).

    mavlink-signing-setup --generate /etc/keelson/mavlink.key --mavlink-url /dev/ttyACM0
    mavlink-signing-setup --key-file /etc/keelson/mavlink.key --mavlink-url /dev/ttyACM0
    mavlink-signing-setup --disable --mavlink-url /dev/ttyACM0

It sends SETUP_SIGNING with the key from the same file mavlink2keelson reads
(``--signing-key-file``). Then it proves the key took: it listens signed, with unsigned frames
refused, and requires a HEARTBEAT from the autopilot that verifies. The autopilot stores the key; the
running connector never sends one.

LOCAL ONLY. A serial device, or loopback (a SITL); any other host is refused, because this is the one
time the secret crosses a wire. Use the autopilot's USB port: ArduPilot accepts unsigned frames there
(SERIAL0) whatever its key, so a vessel keyed by mistake can still be re-keyed or cleared on the bench.

The key is never printed or logged. Exit 0 when the autopilot verifies, 1 when it does not, 2 for a
refusal (a remote link, a key file that cannot be used, an existing file for --generate).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

os.environ["MAVLINK20"] = (
    "1"  # before pymavlink: a signed link is MAVLink 2 from its first frame
)

from pymavlink import mavutil  # noqa: E402

import mavlink_signing  # noqa: E402

log = logging.getLogger("mavlink-signing-setup")

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
_EPOCH_2015 = 1420070400


def link_refusal(url: str):
    """Why ``url`` may not carry a key, or None for a serial device or loopback."""
    if url.startswith(("tlog:", "mcap:")):
        return f"{url}: a replay is not an autopilot"
    scheme, sep, rest = url.partition(":")
    if sep and scheme in {"tcp", "tcpin", "udp", "udpin", "udpout", "udpbcast"}:
        host = rest.rsplit(":", 1)[0]
        if host not in LOOPBACK:
            return (
                f"{url}: the key crosses a wire only at install, over a local link "
                "(the autopilot's USB serial, or loopback to a SITL), never across a network"
            )
    return None


def signing_timestamp() -> int:
    """MAVLink 2 signing time: 10 µs units since 2015-01-01 UTC."""
    return int((max(time.time(), _EPOCH_2015) - _EPOCH_2015) * 100_000)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mavlink-signing-setup",
        description="Key an autopilot for MAVLink 2 signing, once, at install, over a local link "
        "(USB serial, or loopback to a SITL). The key is never printed.",
    )
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--key-file",
        metavar="PATH",
        help="the key mavlink2keelson reads (--signing-key-file)",
    )
    which.add_argument(
        "--generate",
        metavar="PATH",
        help="write a NEW random key to PATH (mode 0600, never overwrites), then key with it",
    )
    which.add_argument(
        "--disable",
        action="store_true",
        help="send the all-zero key: the autopilot stops requiring signatures",
    )
    p.add_argument(
        "--mavlink-url",
        required=True,
        help="a serial device (e.g. /dev/ttyACM0), or tcp:127.0.0.1:PORT for a SITL",
    )
    p.add_argument(
        "--baud", type=int, default=115200, help="serial baud rate (default 115200)"
    )
    p.add_argument(
        "--target-system",
        type=int,
        default=1,
        help="the autopilot's system id (default 1)",
    )
    p.add_argument(
        "--target-component",
        type=int,
        default=1,
        help="the autopilot's component id (default 1)",
    )
    p.add_argument(
        "--source-system",
        type=int,
        default=255,
        help="this tool's system id (default 255)",
    )
    p.add_argument(
        "--source-component",
        type=int,
        default=190,
        help="this tool's component id (default 190)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="seconds to wait for each answer (default 10)",
    )
    return p


def _heartbeat(mav, target_system: int, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hb = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if hb is not None and hb.get_srcSystem() == target_system:
            return hb
    return None


def run(args) -> int:
    why = link_refusal(args.mavlink_url)
    if why:
        log.error("Refusing: %s", why)
        return 2
    try:
        if args.generate:
            mavlink_signing.generate_key(args.generate)
            log.info("Wrote a new key to %s (mode 0600)", args.generate)
        key = (
            bytes(mavlink_signing.KEY_BYTES)
            if args.disable
            else mavlink_signing.load_key(args.key_file or args.generate)
        )
    except FileExistsError:
        log.error(
            "Refusing: %s exists; --generate never overwrites a key", args.generate
        )
        return 2
    except mavlink_signing.SigningKeyError as exc:
        log.error("Refusing: %s", exc)
        return 2

    kwargs = {
        "source_system": args.source_system,
        "source_component": args.source_component,
    }
    if not args.mavlink_url.startswith(("tcp", "udp")):
        kwargs["baud"] = args.baud
    mav = mavutil.mavlink_connection(args.mavlink_url, **kwargs)
    try:
        if _heartbeat(mav, args.target_system, args.timeout) is None:
            log.error(
                "No HEARTBEAT from system %d on %s within %.0f s",
                args.target_system,
                args.mavlink_url,
                args.timeout,
            )
            return 1
        mav.mav.setup_signing_send(
            args.target_system, args.target_component, key, signing_timestamp()
        )
        if args.disable:
            log.info(
                "Sent the all-zero key to system %d: it no longer requires signatures",
                args.target_system,
            )
            return 0
        # Prove it: from here on only frames that verify under the key are accepted.
        mavlink_signing.apply(mav, key, accept_unsigned=False, link_id=0)
        hb = _heartbeat(mav, args.target_system, args.timeout)
        counts = mavlink_signing.counts_of(mav)
        if hb is None or not counts.good:
            log.error(
                "System %d did not answer signed under this key within %.0f s (%d bad signature, "
                "%d unsigned refused): is this port MAVLink 2 (SERIALn_PROTOCOL 2)?",
                args.target_system,
                args.timeout,
                counts.bad,
                counts.rejected,
            )
            return 1
        log.info(
            "Keyed: system %d's frames verify under the key in %s (%d verified)",
            args.target_system,
            args.key_file or args.generate,
            counts.good,
        )
        return 0
    finally:
        mav.close()


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
