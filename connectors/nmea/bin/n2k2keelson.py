#!/usr/bin/env python3

"""
Publish NMEA2000 data from a CAN gateway to Keelson/Zenoh.

Opens the gateway, decodes its NMEA2000 messages and publishes them through
the shared PGN handlers in n2k_handlers.py, which list the supported PGNs.
"""

import sys
import queue
import logging
import argparse
from contextlib import ExitStack

import zenoh
from nmea2000.message import NMEA2000Message

from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    setup_logging,
    GracefulShutdown,
)
from keelson.helpers import enclose_from_bytes

# Sibling modules in this bin/ directory.
import n2k_gateway

# Re-exported so callers and tests can keep addressing them via n2k2keelson.
from n2k_handlers import (  # noqa: F401
    PUBLISHERS,
    PGN_HANDLERS,
    N2K_SUPPORTED_SUBJECTS,
    dispatch_message,
    publish_to_keelson,
    handle_pgn_129025,
    handle_pgn_129026,
    handle_pgn_129029,
    handle_pgn_127250,
    handle_pgn_127251,
    handle_pgn_127257,
    handle_pgn_127258,
    handle_pgn_130306,
    handle_pgn_127245,
    handle_pgn_130311,
    handle_pgn_127488,
    handle_pgn_127489,
    handle_pgn_127505,
    handle_pgn_127506,
    handle_pgn_127508,
    handle_pgn_128259,
    handle_pgn_128267,
    handle_pgn_130312,
    handle_pgn_130316,
    handle_pgn_130313,
    handle_pgn_130314,
    handle_pgn_129038,
    handle_pgn_129039,
    handle_pgn_129794,
    handle_pgn_129539,
    handle_pgn_129540,
)

logger = logging.getLogger("n2k2keelson")


def device_source_id(source_id: str, msg: NMEA2000Message) -> str:
    """The source_id for one device on the bus: the gateway's source_id plus
    the N2K source address of the device that sent ``msg``.

    A gateway sees every device on the bus, and several devices commonly send
    the same PGN (three heading sensors, two GNSS). Keying on the sender keeps
    them apart, the same way $PCDIN/$MXPGN do in nmea01832keelson.
    """
    return f"{source_id}/{msg.source}"


def process_gateway_message(
    msg: NMEA2000Message,
    session,
    realm: str,
    entity_id: str,
    source_id: str,
):
    """Process a single NMEA2000 message received directly from a gateway.

    Publishes under ``<source_id>/<N2K source address>``.
    """
    try:
        logger.debug(f"Received PGN {msg.PGN}: {msg.id} from src {msg.source}")
        device_id = device_source_id(source_id, msg)
        dispatch_message(msg, session, realm, entity_id, device_id)

    except Exception as e:
        logger.error(f"Error processing gateway message: {e}", exc_info=True)


def publish_raw_frame(
    session, realm: str, entity_id: str, source_id: str, time_ns: int, data
):
    """Publish one wire unit, as the gateway sent it, on ``raw_nmea2000``.

    Published under the gateway-level ``source_id``: the raw stream is the
    whole bus, before any frame is attributed to a device. Text formats (e.g.
    a YDEN-02 ``hh:mm:ss.sss R <CAN-ID> <bytes>`` line) are carried as UTF-8.
    """
    if isinstance(data, str):
        data = data.encode()
    elif not isinstance(data, (bytes, bytearray)):
        data = str(data).encode()
    envelope = enclose_from_bytes(bytes(data), time_ns)
    publish_to_keelson(session, realm, entity_id, "raw_nmea2000", source_id, envelope)


class DeviceLiveliness:
    """Liveliness tokens for the bus devices a gateway has heard from.

    Devices are not known at startup, so each gets its source- and
    subject-level tokens the first time one of its messages arrives. Tokens
    are kept until :meth:`close`: a device that goes quiet is still a
    capability of this source.
    """

    def __init__(self, session, realm: str, entity_id: str, pubsub_subjects):
        self._session = session
        self._realm = realm
        self._entity_id = entity_id
        self._pubsub_subjects = list(pubsub_subjects)
        self._stack = ExitStack()
        self._known: set[str] = set()

    def ensure(self, device_id: str) -> bool:
        """Declare tokens for ``device_id`` unless already declared.

        Returns True when the device is new.
        """
        if device_id in self._known:
            return False
        self._stack.enter_context(
            declare_liveliness(
                self._session,
                self._realm,
                self._entity_id,
                device_id,
                pubsub_subjects=self._pubsub_subjects,
            )
        )
        self._known.add(device_id)
        return True

    def close(self):
        self._stack.close()
        self._known.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def parse_pgn_list(pgn_string):
    """Parse a comma-separated PGN string into a list of ints, or None."""
    if not pgn_string:
        return None
    return [int(part.strip()) for part in pgn_string.split(",")]


def run_gateway_mode(session, args):
    """Open a CAN gateway directly, probe its identity, and publish to Keelson.

    The gateway runs on a background thread; this thread waits for the
    connect-time identity probe, fixes the ``source_id`` (appending the probed
    gateway identity), then drains decoded messages onto the bus.
    """
    runner = n2k_gateway.GatewayRunner(
        args.gateway,
        host=args.host,
        port=args.port,
        device=args.device,
        include_pgns=parse_pgn_list(args.include_pgns),
        exclude_pgns=parse_pgn_list(args.exclude_pgns),
        ensure_baud=args.ensure_baud,
        persist=args.persist,
        stream_raw=args.publish_raw,
    )
    runner.start()

    with GracefulShutdown() as shutdown:
        # Wait for the identity probe before fixing the source_id, so the very
        # first published message already carries the gateway identity.
        identity = None
        while not shutdown.is_requested():
            identity = runner.wait_identity(timeout=1.0)
            if identity is not None:
                break
            if not runner.is_running():
                break

        if identity is None:
            if not shutdown.is_requested():
                logger.error("Gateway did not identify itself; shutting down")
            runner.stop()
            return

        # The probed gateway identity becomes the trailing source_id segment(s);
        # each device on the bus then appends its own source address.
        source_id = f"{args.source_id}/{identity.source_id_suffix()}"
        logger.info("Publishing under source_id: %s/<N2K source address>", source_id)

        # The gateway-level source_id carries only the raw bus stream; decoded
        # data is published per device.
        gateway_subjects = ["raw_nmea2000"] if args.publish_raw else []

        with (
            declare_liveliness(
                session,
                args.realm,
                args.entity_id,
                source_id,
                pubsub_subjects=gateway_subjects,
            ),
            DeviceLiveliness(
                session, args.realm, args.entity_id, N2K_SUPPORTED_SUBJECTS
            ) as devices,
        ):
            while not shutdown.is_requested():
                # Raw frames first: the tap sees each unit before it decodes.
                while True:
                    try:
                        time_ns, data = runner.raw_frames.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        publish_raw_frame(
                            session,
                            args.realm,
                            args.entity_id,
                            source_id,
                            time_ns,
                            data,
                        )
                    except Exception as e:
                        logger.error(f"Error publishing raw frame: {e}")
                try:
                    msg = runner.messages.get(timeout=0.05)
                except queue.Empty:
                    continue
                device_id = device_source_id(source_id, msg)
                if devices.ensure(device_id):
                    logger.info(
                        "New N2K device on bus: src=%s -> %s", msg.source, device_id
                    )
                process_gateway_message(
                    msg,
                    session,
                    args.realm,
                    args.entity_id,
                    source_id,
                )

    runner.stop()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Publish NMEA2000 data from a CAN gateway to Keelson/Zenoh",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Add common Zenoh arguments from scaffolding
    add_common_arguments(parser)

    # Required arguments
    parser.add_argument(
        "-r",
        "--realm",
        required=True,
        help="Keelson realm (e.g., 'vessel/sv_colibri')",
    )
    parser.add_argument(
        "-e",
        "--entity-id",
        required=True,
        help="Entity identifier (e.g., 'sensors')",
    )
    parser.add_argument(
        "-s",
        "--source-id",
        required=True,
        help="Base source identifier (e.g., 'n2k/primary'). The probed gateway "
        "identity is appended as '<type>/<address>', then the N2K source address "
        "of each device on the bus.",
    )

    # Optional arguments
    parser.add_argument(
        "--publish-raw",
        action="store_true",
        help="Also publish every frame read from the gateway, undecoded, on "
        "'raw_nmea2000' under the gateway-level source_id",
    )

    # CAN gateway selection.
    gateway_group = parser.add_argument_group("CAN gateway")
    gateway_group.add_argument(
        "--gateway",
        required=True,
        choices=sorted(n2k_gateway.GATEWAY_PROFILES),
        help="CAN gateway profile to open.",
    )
    gateway_group.add_argument("--host", help="Gateway host (TCP gateway profiles)")
    gateway_group.add_argument(
        "--port", type=int, help="Gateway TCP port (TCP gateway profiles)"
    )
    gateway_group.add_argument(
        "--device", help="Gateway serial device path (USB gateway profiles)"
    )
    gateway_group.add_argument(
        "--include-pgns", help="Comma-separated list of PGNs to include"
    )
    gateway_group.add_argument(
        "--exclude-pgns", help="Comma-separated list of PGNs to exclude"
    )
    gateway_group.add_argument(
        "--ensure-baud",
        type=int,
        default=115200,
        help="NGX-1 target serial baud rate (actisense_ngx1 only)",
    )
    gateway_group.add_argument(
        "--persist",
        action="store_true",
        help="Persist NGX-1 configuration to EEPROM (actisense_ngx1 only)",
    )

    args = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=args.log_level)

    logger.info("Starting n2k2keelson")
    logger.info(f"Realm: {args.realm}")
    logger.info(f"Entity ID: {args.entity_id}")
    logger.info(f"Source ID: {args.source_id}")
    logger.info(f"Supported PGNs: {sorted(PGN_HANDLERS.keys())}")

    # Configure Zenoh using scaffolding
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    # Open Zenoh session
    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)
    logger.info("Zenoh session opened")

    try:
        logger.info(f"Gateway: {args.gateway}")
        run_gateway_mode(session, args)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Closing Zenoh session...")
        session.close()
        logger.info("Session closed")


if __name__ == "__main__":
    main()
