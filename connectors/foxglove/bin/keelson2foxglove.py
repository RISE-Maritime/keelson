#!/usr/bin/env python3

import sys
import pathlib
import logging
import argparse
import functools
from typing import Dict
from queue import Queue, Empty
from threading import Thread

import zenoh
import keelson
import keelson.interfaces
import foxglove
from foxglove import Channel, Schema
from foxglove.websocket import (
    Capability,
    ChannelView,
    Client,
    MessageSchema,
    Service,
    ServiceRequest,
    ServiceSchema,
    ServerListener,
)
from google.protobuf.message import DecodeError
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
    suppress_exception,
    check_queue_backpressure,
    GracefulShutdown,
    LivelinessMonitor,
)

logger = logging.getLogger("foxglove-liveview")

MAIN_LOOP_SLEEP_TIME = 10.0  # seconds


class KeelsonListener(ServerListener):
    def __init__(self) -> None:
        # Map client id -> set of subscribed topics
        self.subscribers: dict[int, set[str]] = {}

    def has_subscribers(self) -> bool:
        return len(self.subscribers) > 0

    def on_subscribe(
        self,
        client: Client,
        channel: ChannelView,
    ) -> None:
        """
        Called by the server when a client subscribes to a channel.
        We'll use this and on_unsubscribe to simply track if we have any subscribers at all.
        """
        logging.info(f"Client {client} subscribed to channel {channel.topic}")
        self.subscribers.setdefault(client.id, set()).add(channel.topic)

    def on_unsubscribe(
        self,
        client: Client,
        channel: ChannelView,
    ) -> None:
        """
        Called by the server when a client unsubscribes from a channel.
        """
        logging.info(f"Client {client} unsubscribed from channel {channel.topic}")
        self.subscribers[client.id].remove(channel.topic)
        if not self.subscribers[client.id]:
            del self.subscribers[client.id]


class RpcServiceBridge:
    """Advertises live keelson RPC endpoints as callable Foxglove services.

    Discovery is liveliness-driven: an interface-level liveliness token
    joining/leaving the bus adds/removes the corresponding set of Foxglove
    services. Only interfaces registered in this SDK's ``interfaces.yaml``
    can be advertised (unknown interfaces are skipped with a warning).

    Note: service handlers run ``invoke_procedure`` synchronously
    (blocking up to ``timeout``) on the foxglove server's own handler
    thread. This is acceptable for v1 and matches keelson's single-reply
    RPC semantics; a slow/unresponsive responder will hold up that one
    handler thread but not the rest of the bridge.
    """

    def __init__(self, session: zenoh.Session, server: foxglove.WebSocketServer):
        self._session = session
        self._server = server
        self._fds_bytes = keelson.interfaces.get_interfaces_file_descriptor_set()
        # token_key -> list of Foxglove service names advertised for it
        self._services: Dict[str, list] = {}

    def on_join(self, token_key: str) -> None:
        # Guard against a rejoin after a transient reconnect re-declaring
        # the same liveliness token — treat it as a no-op.
        if token_key in self._services:
            return

        try:
            parsed = keelson.parse_rpc_interface_liveliness_key(token_key)
        except ValueError:
            logger.debug(
                "Liveliness key %s did not match the RPC interface pattern; ignoring",
                token_key,
            )
            return

        base_path = parsed["base_path"]
        entity_id = parsed["entity_id"]
        interface = parsed["interface"]
        version = parsed["version"]
        source_id = parsed["source_id"]

        if not keelson.is_interface_well_known(f"{interface}/{version}"):
            logger.warning(
                "Live interface %s/%s (entity=%s, source=%s) is not in this "
                "SDK's interfaces.yaml — skipping",
                interface,
                version,
                entity_id,
                source_id,
            )
            # Record an empty entry so we don't re-warn on rejoin.
            self._services[token_key] = []
            return

        services = []
        names = []
        for procedure in keelson.interfaces.get_procedures(interface, version):
            request_desc, response_desc = keelson.interfaces.get_procedure_schemas(
                interface, version, procedure
            )
            name = f"{entity_id}/{interface}/{version}/{procedure}/{source_id}"
            schema = ServiceSchema(
                name=name,
                request=MessageSchema(
                    encoding="protobuf",
                    schema=Schema(
                        name=request_desc.full_name,
                        encoding="protobuf",
                        data=self._fds_bytes,
                    ),
                ),
                response=MessageSchema(
                    encoding="protobuf",
                    schema=Schema(
                        name=response_desc.full_name,
                        encoding="protobuf",
                        data=self._fds_bytes,
                    ),
                ),
            )
            handler = functools.partial(
                self._call,
                base_path,
                entity_id,
                interface,
                version,
                procedure,
                source_id,
            )
            services.append(Service(name=name, schema=schema, handler=handler))
            names.append(name)

        self._server.add_services(services)
        self._services[token_key] = names
        logger.info(
            "Advertised %d Foxglove services for %s/%s/%s/%s",
            len(names),
            entity_id,
            interface,
            version,
            source_id,
        )

    def _call(
        self,
        base_path: str,
        entity_id: str,
        interface: str,
        version: str,
        procedure: str,
        source_id: str,
        request: ServiceRequest,
    ) -> bytes:
        response = keelson.interfaces.invoke_procedure(
            self._session,
            base_path,
            entity_id,
            interface,
            version,
            procedure,
            source_id,
            request=bytes(request.payload),
            timeout=10.0,
        )
        return response.SerializeToString()

    def on_leave(self, token_key: str) -> None:
        names = self._services.pop(token_key, None)
        if not names:
            return
        self._server.remove_services(names)
        logger.info("Removed %d Foxglove services for %s", len(names), token_key)

    def close(self) -> None:
        """Undeclare/remove every outstanding Foxglove service."""
        all_names = []
        for names in self._services.values():
            all_names.extend(names)
        self._services.clear()
        if all_names:
            self._server.remove_services(all_names)


def run(session: zenoh.Session, args: argparse.Namespace):

    logger.info("Starting ws server on %s:%s", args.ws_host, args.ws_port)

    listener = KeelsonListener()

    capabilities = [Capability.ClientPublish]
    if args.expose_rpc_services:
        capabilities.append(Capability.Services)

    server = foxglove.start_server(
        host=args.ws_host,
        port=args.ws_port,
        server_listener=listener,
        capabilities=capabilities,
        supported_encodings=["protobuf"],
    )

    queue = Queue()

    # Wired up only when --expose-rpc-services is passed; everything below
    # is a no-op otherwise so existing behavior is untouched.
    rpc_bridge = None
    rpc_monitors: list = []
    if args.expose_rpc_services:
        rpc_bridge = RpcServiceBridge(session, server)
        for base_path in args.expose_rpc_services:
            pattern = f"{base_path}/@v0/*/@rpc/**"
            logger.info("Monitoring RPC liveliness on: %s", pattern)
            rpc_monitors.append(
                LivelinessMonitor(
                    session,
                    pattern,
                    on_join=rpc_bridge.on_join,
                    on_leave=rpc_bridge.on_leave,
                    history=True,
                )
            )

    with GracefulShutdown() as shutdown:

        def _ws_publisher():
            channels: Dict[str, Channel] = {}

            # Keep draining after shutdown is requested so the main thread's
            # cleanup doesn't hang waiting for an empty queue we ourselves
            # stopped consuming. Only exit when shutdown AND the queue is
            # empty.
            while not shutdown.is_requested() or not queue.empty():
                with suppress_exception(Exception, context="ws publisher"):
                    try:
                        sample: zenoh.Sample = queue.get(timeout=0.01)
                    except Empty:
                        continue

                    key = str(sample.key_expr)
                    logger.debug("Received sample on key: %s", key)

                    # if not listener.has_subscribers():
                    #     logger.debug("No listeners, doing nothing!")
                    #     continue

                    # Uncover from keelson envelope
                    try:
                        received_at, enclosed_at, payload = keelson.uncover(
                            sample.payload.to_bytes()
                        )
                    except DecodeError:
                        logger.exception(
                            "Key %s did not contain a valid keelson.Envelope: %s",
                            key,
                            sample.payload.to_bytes(),
                        )
                        continue

                    # If this key is known, write message to file
                    if key in channels:
                        logger.debug("Key %s is already known!", key)
                        channels[key].log(
                            payload, log_time=received_at, publish_time=enclosed_at
                        )
                        continue

                    # Else, lets start finding out about schemas etc
                    try:
                        subject = keelson.get_subject_from_pubsub_key(key)
                    except ValueError:
                        logger.exception(
                            "Received key did not match the expected format: %s",
                            key,
                        )
                        continue

                    logger.info("Unseen key: %s", key)

                    if not keelson.is_subject_well_known(subject):
                        logger.info("Unknown subject, skipping...")
                        continue

                    logger.info("Subject %s is well-known!", subject)
                    # Get info about the well-known subject
                    keelson_schema = keelson.get_subject_schema(subject)

                    file_descriptor_set = (
                        keelson.get_protobuf_file_descriptor_set_from_type_name(
                            keelson_schema
                        )
                    )

                    logger.debug(
                        "Registering a channel (%s) with schema_name=%s",
                        key,
                        keelson_schema,
                    )

                    channel = channels[key] = Channel(
                        topic=key,
                        message_encoding="protobuf",
                        schema=Schema(
                            name=keelson_schema,
                            encoding="protobuf",
                            data=file_descriptor_set.SerializeToString(),
                        ),
                    )

                    # Finally, write the message to the socket
                    logger.debug("...and writing the actual message to file!")
                    channel.log(payload, log_time=received_at, publish_time=enclosed_at)

        ws_publisher_thread = Thread(target=_ws_publisher)
        ws_publisher_thread.daemon = True
        ws_publisher_thread.start()

        # And start subscribing
        logger.info("Starting subscribers")
        subscribers = [session.declare_subscriber(key, queue.put) for key in args.key]

        while not shutdown.is_requested():
            # Check queue size
            check_queue_backpressure(queue, context="ws publisher")

            shutdown.wait(timeout=MAIN_LOOP_SLEEP_TIME)

        # Graceful shutdown
        logger.info("Closing down on user request!")
        logger.debug("Undeclaring subscribers...")
        for sub in subscribers:
            sub.undeclare()

        # Publisher thread now drains the queue itself (see _ws_publisher),
        # so we just wait for it to finish. Bounded join so a hung consumer
        # (e.g. a Foxglove client holding up the server) can't trap shutdown.
        logger.debug("Joining websocket publisher thread...")
        ws_publisher_thread.join(timeout=5.0)
        if ws_publisher_thread.is_alive():
            logger.warning("ws publisher thread did not exit within 5s; abandoning")

        if rpc_monitors:
            logger.debug("Closing RPC liveliness monitors...")
            for monitor in rpc_monitors:
                monitor.close()
        if rpc_bridge is not None:
            logger.debug("Closing RPC service bridge...")
            rpc_bridge.close()

        logger.debug("Stopping websocket server...")
        server.stop()

        logger.debug("Done! Good bye :)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="keelson2foxglove",
        description="A foxglove websocket server for keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)

    parser.add_argument(
        "-k",
        "--key",
        type=str,
        action="append",
        required=True,
        help="Key expressions to subscribe to from the Zenoh session",
    )

    parser.add_argument("--ws-host", type=str, default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=8765)

    def _parse_pair(arg) -> tuple[pathlib.Path, pathlib.Path]:
        path_to_subject_yaml, path_to_proto_types = arg.split(",")
        return pathlib.Path(path_to_subject_yaml), (
            pathlib.Path(path_to_proto_types) if path_to_proto_types else None
        )

    parser.add_argument(
        "--extra-subjects-types",
        type=_parse_pair,
        action="append",
        help="Add additional well-known subjects and protobuf types as --extra-subjects-types=path/to/subjects.yaml,path_to_protobuf_file_descriptor_set.bin",
    )

    parser.add_argument(
        "--expose-rpc-services",
        type=str,
        action="append",
        default=None,
        help="Advertise all live keelson RPC endpoints under this base path as Foxglove services",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)
    foxglove.set_log_level(args.log_level)

    # Loading extra well-known subjects and types if provided
    if extra_paths := args.extra_subjects_types:
        for pair in extra_paths:
            logger.info("Loading extra subjects (%s) and types (%s)", *pair)
            keelson.add_well_known_subjects_and_proto_definitions(*pair)

    # Put together zenoh session configuration
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    # Construct session
    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        try:
            run(session, args)
        except KeyboardInterrupt:
            logger.info("Closing down on user request!")
            sys.exit(0)
