"""Shared CLI argument patterns for Keelson connectors."""

import json
import logging
import argparse
from typing import Optional, List

import zenoh


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common CLI arguments used by most Keelson connectors.

    Adds the following arguments:
        --log-level: Logging level (default: INFO)
        --mode: Zenoh session mode (peer/client)
        --connect: Zenoh endpoints to connect to
        --listen: Zenoh endpoints to listen on

    Args:
        parser: The argument parser to add arguments to.
    """
    parser.add_argument(
        "--log-level",
        type=int,
        default=logging.INFO,
        help="Logging level (default: INFO)",
    )

    parser.add_argument(
        "--mode",
        "-m",
        dest="mode",
        choices=["peer", "client"],
        type=str,
        help="The Zenoh session mode.",
    )

    parser.add_argument(
        "--connect",
        action="append",
        type=str,
        help="Endpoints to connect to. Example: tcp/localhost:7447",
    )

    parser.add_argument(
        "--listen",
        action="append",
        type=str,
        help="Endpoints to listen on. Example: tcp/0.0.0.0:7447",
    )


def create_zenoh_config(
    mode: Optional[str] = None,
    connect: Optional[List[str]] = None,
    listen: Optional[List[str]] = None,
) -> zenoh.Config:
    """Create a Zenoh configuration from common CLI arguments.

    Args:
        mode: Zenoh session mode (peer/client).
        connect: List of endpoints to connect to.
        listen: List of endpoints to listen on.

    Returns:
        A configured zenoh.Config object.
    """
    conf = zenoh.Config()

    if mode is not None:
        conf.insert_json5("mode", json.dumps(mode))
    if connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(connect))
    if listen is not None:
        conf.insert_json5("listen/endpoints", json.dumps(listen))

    return conf
