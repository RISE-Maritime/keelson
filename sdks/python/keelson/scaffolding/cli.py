"""Shared CLI argument patterns for Keelson applications."""

import json
import logging
import argparse
import os
from typing import Optional, List

import zenoh


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common CLI arguments used by most Keelson applications.

    Adds the following arguments:
        --log-level: Logging level (default: INFO)
        --mode: Zenoh session mode (peer/client)
        --connect: Zenoh endpoints to connect to
        --listen: Zenoh endpoints to listen on
        --zenoh-config: Path to a Zenoh configuration file

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

    # Deliberately not --config: composite_aggregator, entity_health and
    # labjack already use that for their own JSON, and hand_controller uses
    # -c, so adding it here would raise on import in four connectors.
    parser.add_argument(
        "--zenoh-config",
        type=str,
        default=None,
        help="Path to a Zenoh configuration file (JSON5). Everything the "
        "flags above cannot express — access control, QoS defaults, transport "
        "tuning — lives here. --mode/--connect/--listen still win where they "
        "overlap. Falls back to the ZENOH_CONFIG environment variable.",
    )


def create_zenoh_config(
    mode: Optional[str] = None,
    connect: Optional[List[str]] = None,
    listen: Optional[List[str]] = None,
    zenoh_config: Optional[str] = None,
) -> zenoh.Config:
    """Create a Zenoh configuration from common CLI arguments.

    The base is a configuration file when one is given, and Zenoh's defaults
    otherwise. The flags are applied on top, so an operator can hand a
    connector a file describing everything the CLI cannot express and still
    redirect it at a different router on the command line.

    A file is looked for in two places, in order: the ``zenoh_config``
    argument (``--zenoh-config``), then the ``ZENOH_CONFIG`` environment
    variable Zenoh itself defines. The environment path means a connector that
    has not been passed the flag can still be configured by its deployment.

    Args:
        mode: Zenoh session mode (peer/client).
        connect: List of endpoints to connect to.
        listen: List of endpoints to listen on.
        zenoh_config: Path to a Zenoh configuration file (JSON5).

    Returns:
        A configured zenoh.Config object.

    Raises:
        zenoh.ZError: if the configuration file cannot be read or parsed.
    """
    if zenoh_config is not None:
        conf = zenoh.Config.from_file(zenoh_config)
    elif os.environ.get(zenoh.Config.DEFAULT_CONFIG_PATH_ENV):
        conf = zenoh.Config.from_env()
    else:
        conf = zenoh.Config()

    if mode is not None:
        conf.insert_json5("mode", json.dumps(mode))
    if connect is not None:
        conf.insert_json5("connect/endpoints", json.dumps(connect))
    if listen is not None:
        conf.insert_json5("listen/endpoints", json.dumps(listen))

    return conf
