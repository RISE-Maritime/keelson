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
from .liveliness import (
    LivelinessMonitor,
    PubsubSubjectLivelinessManager,
    declare_liveliness,
    declare_liveliness_token,
    declare_pubsub_subject_liveliness,
    declare_rpc_interface_liveliness,
    declare_source_liveliness,
)
from .logging import setup_logging
from .qos_zenoh import (
    declare_publisher,
    declare_publisher_for_subject,
    put,
    zenoh_publisher_kwargs,
)
from .queue_utils import check_queue_backpressure
from .rpc import ReplyTracker, RpcOp, RpcServer, reply_err, serve_rpc
from .signals import GracefulShutdown

__all__ = [
    "add_common_arguments",
    "check_queue_backpressure",
    "create_zenoh_config",
    "declare_liveliness",
    "declare_liveliness_token",
    "declare_publisher",
    "declare_publisher_for_subject",
    "declare_pubsub_subject_liveliness",
    "declare_rpc_interface_liveliness",
    "declare_source_liveliness",
    "GracefulShutdown",
    "LivelinessMonitor",
    "PubsubSubjectLivelinessManager",
    "make_configurable",
    "put",
    "reply_err",
    "ReplyTracker",
    "RpcOp",
    "RpcServer",
    "serve_rpc",
    "setup_logging",
    "suppress_exception",
    "zenoh_publisher_kwargs",
]
