"""Application scaffolding utilities for Keelson applications.

This module provides common patterns and utilities for building applications
on top of Keelson, including:

- CLI argument parsing and Zenoh configuration
- Logging setup
- Graceful shutdown handling
- Queue backpressure monitoring
- Exception handling utilities
- Configurable interface (RPC-based configuration)

These utilities are designed for any Keelson application type:
- Connectors (ingest/export data)
- Processors (transform data streams)
- Services (RPC responders)
- Any long-running Keelson application
"""

from .cli import add_common_arguments, create_zenoh_config
from .configurable import make_configurable
from .exceptions import suppress_exception
from .liveliness import LivelinessMonitor, LivelinessToken, declare_liveliness_token
from .logging import setup_logging
from .queue_utils import check_queue_backpressure
from .signals import GracefulShutdown

__all__ = [
    "add_common_arguments",
    "check_queue_backpressure",
    "create_zenoh_config",
    "declare_liveliness_token",
    "GracefulShutdown",
    "LivelinessMonitor",
    "LivelinessToken",
    "make_configurable",
    "setup_logging",
    "suppress_exception",
]
