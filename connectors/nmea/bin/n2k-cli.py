#!/usr/bin/env python3

"""
N2K-CLI: NMEA2000 CAN Gateway Bridge

Bidirectional gateway between NMEA2000 CAN bus and JSON streams.

Read mode (CAN → JSON):
  Connects to CAN gateway, decodes NMEA2000 messages, outputs JSON to STDOUT

Write mode (JSON → CAN):
  Reads JSON from STDIN, encodes to NMEA2000, sends to CAN gateway

Bidirectional mode:
  Simultaneous read and write operations
"""

import sys
import json
import asyncio
import logging
import argparse
import signal
from typing import Optional
from enum import Enum

# Import nmea2000 library components
from nmea2000.message import NMEA2000Message
from nmea2000.ioclient import (
    AsyncIOClient,
    EByteNmea2000Gateway,
    ActisenseNmea2000Gateway,
    YachtDevicesNmea2000Gateway,
    WaveShareNmea2000Gateway,
    State,
)

# Configure logging to stderr only (stdout is for data)
logging.basicConfig(
    stream=sys.stderr,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("n2k-cli")


class GatewayType(Enum):
    """Types of CAN gateway connections"""

    TCP = "tcp"
    USB = "usb"


class Protocol(Enum):
    """CAN gateway protocols"""

    EBYTE = "ebyte"
    ACTISENSE = "actisense"
    YACHT_DEVICES = "yacht_devices"
    WAVESHARE = "waveshare"


class N2KCLIReader:
    """Handles reading from CAN gateway and outputting JSON"""

    def __init__(
        self,
        client: AsyncIOClient,
        include_pgns: Optional[list] = None,
        exclude_pgns: Optional[list] = None,
    ):
        self.client = client
        self.include_pgns = set(include_pgns) if include_pgns else set()
        self.exclude_pgns = set(exclude_pgns) if exclude_pgns else set()
        self.running = True

    async def handle_received_message(self, message: NMEA2000Message):
        """Callback for received NMEA2000 messages"""
        try:
            # Apply PGN filtering
            if self.include_pgns and message.PGN not in self.include_pgns:
                return
            if self.exclude_pgns and message.PGN in self.exclude_pgns:
                return

            # Convert to JSON and output to stdout
            json_str = message.to_json()
            sys.stdout.write(json_str + "\n")
            sys.stdout.flush()

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def handle_status_change(self, state: State):
        """Callback for connection status changes"""
        logger.info(f"Connection status: {state}")

    async def run(self):
        """Main read loop"""
        self.client.set_receive_callback(self.handle_received_message)
        self.client.set_status_callback(self.handle_status_change)

        logger.info("Connecting to CAN gateway...")
        await self.client.connect()
        logger.info("Connected. Reading messages...")

        try:
            # Keep running until interrupted
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Read operation cancelled")
        finally:
            await self.client.close()
            logger.info("Connection closed")

    def stop(self):
        """Stop the reader"""
        self.running = False


class N2KCLIWriter:
    """Handles reading JSON from STDIN and writing to CAN gateway"""

    def __init__(self, client: AsyncIOClient):
        self.client = client
        self.running = True

    async def handle_status_change(self, state: State):
        """Callback for connection status changes"""
        logger.info(f"Connection status: {state}")

    async def run(self):
        """Main write loop"""
        self.client.set_status_callback(self.handle_status_change)

        logger.info("Connecting to CAN gateway...")
        await self.client.connect()
        logger.info("Connected. Reading JSON from STDIN...")

        try:
            # Read from stdin asynchronously
            while self.running:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    logger.info("EOF on STDIN")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    # Parse JSON and create NMEA2000Message
                    message = NMEA2000Message.from_json(line)
                    await self.client.send(message)
                    logger.debug(f"Sent PGN {message.PGN}")

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                except Exception as e:
                    logger.error(f"Error sending message: {e}")

        except asyncio.CancelledError:
            logger.info("Write operation cancelled")
        finally:
            await self.client.close()
            logger.info("Connection closed")

    def stop(self):
        """Stop the writer"""
        self.running = False


class N2KCLIBidirectional:
    """Handles bidirectional communication"""

    def __init__(
        self,
        client: AsyncIOClient,
        include_pgns: Optional[list] = None,
        exclude_pgns: Optional[list] = None,
    ):
        self.reader = N2KCLIReader(client, include_pgns, exclude_pgns)
        self.writer = N2KCLIWriter(client)

    async def run(self):
        """Run both reader and writer concurrently"""
        logger.info("Starting bidirectional mode...")

        # Run both reader and writer tasks concurrently
        reader_task = asyncio.create_task(self.reader.run())
        writer_task = asyncio.create_task(self.writer.run())

        try:
            await asyncio.gather(reader_task, writer_task)
        except asyncio.CancelledError:
            logger.info("Bidirectional operation cancelled")
            self.reader.stop()
            self.writer.stop()
            await asyncio.gather(reader_task, writer_task, return_exceptions=True)

    def stop(self):
        """Stop both reader and writer"""
        self.reader.stop()
        self.writer.stop()


def create_client(args) -> AsyncIOClient:
    """Create appropriate CAN gateway client based on arguments"""

    if args.gateway_type == GatewayType.TCP:
        # TCP-based gateway
        if not args.host or not args.port:
            raise ValueError("--host and --port required for TCP gateway")

        if args.protocol == Protocol.EBYTE:
            logger.info(f"Creating EBYTE client for {args.host}:{args.port}")
            return EByteNmea2000Gateway(args.host, args.port)
        elif args.protocol == Protocol.ACTISENSE:
            logger.info(f"Creating Actisense client for {args.host}:{args.port}")
            return ActisenseNmea2000Gateway(args.host, args.port)
        elif args.protocol == Protocol.YACHT_DEVICES:
            logger.info(f"Creating Yacht Devices client for {args.host}:{args.port}")
            return YachtDevicesNmea2000Gateway(args.host, args.port)
        else:
            raise ValueError(f"Unsupported protocol for TCP: {args.protocol}")

    elif args.gateway_type == GatewayType.USB:
        # USB-based gateway
        if not args.port:
            raise ValueError("--port (serial port) required for USB gateway")

        if args.protocol == Protocol.WAVESHARE:
            logger.info(f"Creating Waveshare USB client on {args.port}")
            return WaveShareNmea2000Gateway(port=args.port)
        else:
            raise ValueError(f"Unsupported protocol for USB: {args.protocol}")

    else:
        raise ValueError(f"Unsupported gateway type: {args.gateway_type}")


def parse_pgn_list(pgn_string: Optional[str]) -> Optional[list]:
    """Parse comma-separated PGN list"""
    if not pgn_string:
        return None
    try:
        return [int(pgn.strip()) for pgn in pgn_string.split(",")]
    except ValueError as e:
        raise ValueError(f"Invalid PGN list: {e}")


async def main_async():
    """Main async entry point"""
    parser = argparse.ArgumentParser(
        description="N2K-CLI: NMEA2000 CAN Gateway Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Subcommands
    subparsers = parser.add_subparsers(
        dest="mode", required=True, help="Operation mode"
    )

    # Read mode
    read_parser = subparsers.add_parser(
        "read", help="Read from CAN gateway, output JSON to STDOUT"
    )

    # Write mode
    write_parser = subparsers.add_parser(
        "write", help="Read JSON from STDIN, write to CAN gateway"
    )

    # Bidirectional mode
    bidir_parser = subparsers.add_parser("bidirectional", help="Bidirectional mode")

    # Common arguments for all modes
    for subparser in [read_parser, write_parser, bidir_parser]:
        subparser.add_argument(
            "--gateway-type",
            type=lambda x: GatewayType(x),
            choices=list(GatewayType),
            required=True,
            help="Gateway connection type",
        )
        subparser.add_argument(
            "--protocol",
            type=lambda x: Protocol(x),
            choices=list(Protocol),
            required=True,
            help="CAN gateway protocol",
        )
        subparser.add_argument(
            "--host",
            type=str,
            help="Gateway host (for TCP)",
        )
        subparser.add_argument(
            "--port",
            type=str,
            help="Gateway port (TCP port number or serial device path)",
        )
        subparser.add_argument(
            "--log-level",
            type=str,
            default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            help="Logging level (default: INFO)",
        )

    # Filtering arguments for read and bidirectional modes
    for subparser in [read_parser, bidir_parser]:
        subparser.add_argument(
            "--include-pgns",
            type=str,
            help="Comma-separated list of PGNs to include (e.g., '129025,129026')",
        )
        subparser.add_argument(
            "--exclude-pgns",
            type=str,
            help="Comma-separated list of PGNs to exclude",
        )

    args = parser.parse_args()

    # Set logging level
    logger.setLevel(getattr(logging, args.log_level))

    # Parse PGN filters
    include_pgns = None
    exclude_pgns = None
    if hasattr(args, "include_pgns"):
        include_pgns = parse_pgn_list(args.include_pgns)
    if hasattr(args, "exclude_pgns"):
        exclude_pgns = parse_pgn_list(args.exclude_pgns)

    # Create client
    try:
        client = create_client(args)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Create appropriate handler based on mode
    handler = None
    if args.mode == "read":
        handler = N2KCLIReader(client, include_pgns, exclude_pgns)
    elif args.mode == "write":
        handler = N2KCLIWriter(client)
    elif args.mode == "bidirectional":
        handler = N2KCLIBidirectional(client, include_pgns, exclude_pgns)

    # Set up signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        if handler:
            handler.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the handler
    try:
        await handler.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def main():
    """Main entry point"""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
