"""Configurable interface utilities for Keelson applications."""

import json
import time
import logging
from typing import Callable

import zenoh

from keelson import enclose, construct_pubsub_key
from keelson.payloads.Primitives_pb2 import TimestampedString
from keelson.interfaces.Configurable_pb2 import ConfigurableSuccessResponse

from .rpc import RpcOp, RpcServer, serve_rpc

logger = logging.getLogger(__name__)


def make_configurable(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    responder_id: str,
    get_config_cb: Callable[[], dict],
    set_config_cb: Callable[[dict], None],
) -> RpcServer:
    """Serve the ``configurable/v1`` RPC interface for a Keelson application.

    This sets up RPC queryables for the get_config and set_config
    procedures (plus the interface-level liveliness token), allowing
    remote configuration of the application. Every applied configuration
    is also republished on the ``configuration_json`` subject.

    Args:
        session: Active Zenoh session.
        base_path: Base path for Keelson keys.
        entity_id: Entity identifier.
        responder_id: Responder identifier for RPC.
        get_config_cb: Callback that returns current configuration as dict.
        set_config_cb: Callback to apply new configuration from dict.
    """
    # Declaring a publisher for subject=`configuration_json`
    _publisher = session.declare_publisher(
        construct_pubsub_key(base_path, entity_id, "configuration_json", responder_id)
    )

    def _get_config(op: RpcOp):
        # The reply is an actual JSON string, not a protobuf type — see
        # the JSON placeholder message in Configurable.proto.
        op.reply_ok(json.dumps(get_config_cb()).encode())

    def _set_config(op: RpcOp):
        try:
            set_config_cb(json.loads(op.request_bytes))
            op.reply_ok(ConfigurableSuccessResponse())
        except Exception as exc:
            logger.exception(
                "Failed to respond to query with payload: %s", op.request_bytes
            )
            op.reply_err(str(exc))
        finally:
            # Publish updated config to ensure we log it
            payload = TimestampedString()
            payload.timestamp.FromNanoseconds(time.time_ns())
            payload.value = json.dumps(get_config_cb())
            logger.debug("Publishing new configuration to %s", _publisher.key_expr)
            _publisher.put(enclose(payload.SerializeToString()))

    return serve_rpc(
        session,
        base_path=base_path,
        entity_id=entity_id,
        responder_id=responder_id,
        interface="configurable",
        version="v1",
        handlers={"get_config": _get_config, "set_config": _set_config},
        log=logger,
    )
