# Helper functions that should be moved to keelson-sdk?
import json
import time
import logging
from typing import Callable

import zenoh

from keelson import enclose, construct_pubsub_key, construct_rpc_key
from keelson.payloads.Primitives_pb2 import TimestampedString
from keelson.interfaces.Configurable_pb2 import ConfigurableSuccessResponse
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

logger = logging.getLogger(__file__)


def make_configurable(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    responder_id: str,
    get_config_cb: Callable[[], dict],
    set_config_cb: Callable[[dict], None],
):

    # Create the key for procedure=`get_config`
    _get_config_key = construct_rpc_key(
        base_path, entity_id, "get_config", responder_id
    )

    # Internal callback for `get_config` queryable
    def _get_config(query: zenoh.Query):
        logger.debug("Received query on: %s", query.key_expr)
        logger.debug("Returning current config on key: %s", _get_config_key)
        query.reply(_get_config_key, json.dumps(get_config_cb()))

    # Declaring the queryable
    session.declare_queryable(_get_config_key, _get_config, complete=True)

    # Declaring a publisher for subject=`configuration_json`
    _publisher = session.declare_publisher(
        construct_pubsub_key(base_path, entity_id, "configuration_json", responder_id)
    )

    # Create the key for procedure=`set_config`
    _set_config_key = construct_rpc_key(
        base_path, entity_id, "set_config", responder_id
    )

    # Internal callback for `set_config` queryable
    def _set_config(query: zenoh.Query):
        try:
            logger.debug("Received query on: %s", query.key_expr)
            logger.debug("Replying on key: %s", _set_config_key)

            logger.debug("Calling `set_config_cb`")
            set_config_cb(json.loads(query.payload.to_bytes()))

            query.reply(
                _set_config_key, ConfigurableSuccessResponse().SerializeToString()
            )

        except Exception as exc:
            logger.exception(
                "Failed to respond to query with payload: %s", query.payload
            )
            query.reply_err(
                ErrorResponse(error_description=str(exc)).SerializeToString()
            )

        finally:
            # Publish updated config to ensure we log it
            payload = TimestampedString()
            payload.timestamp.FromNanoseconds(time.time_ns())
            payload.value = json.dumps(get_config_cb())
            logger.debug("Publishing new configuration to %s", _publisher.key_expr)
            logger.debug(payload)
            _publisher.put(enclose(payload.SerializeToString()))

    session.declare_queryable(_set_config_key, _set_config, complete=True)
