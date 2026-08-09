#!/usr/bin/env python3
"""Keelson network manager: answers ping_network, pings peers, publishes network_status.

Run one per site. Each instance plays both roles, which is what makes a link
measurable at all — a ping needs a responder at the far end, so a
responder-only deployment can never measure anything itself.

    network_manager2keelson --realm rise --entity-id ted \
        --source-id network --peers masslab,sf18

    declares   rise/@v0/ted/@rpc/ping_network/network
    pings      rise/@v0/{peer}/@rpc/ping_network/*
    publishes  rise/@v0/ted/pubsub/network_status/network

The published subject is `network_status` (`keelson.NetworkStatus`), which
carries both ends of the link in its payload — so the publish key stays plain
rather than using `@target/{peer}`. A wildcard cannot cross an `@`-segment, so
a plain key lets a consumer collect every link with one subscription.

This replaces keelson-processor-network-manager, which spoke the pre-`@v0` key
format and imported a payload type (`payloads/NetworkPing`) that keelson has
since deleted. Living in the monorepo is the point: it can no longer drift away
from the definitions it depends on.
"""

import argparse
import json
import logging
import time
from typing import Iterable

import zenoh

import keelson
from keelson.interfaces.NetworkPingPong_pb2 import NetworkPing, NetworkPong
from keelson.payloads.NetworkStatus_pb2 import NetworkStatus

from network_manager.pingpong import compute

logger = logging.getLogger("network_manager")

# Interface identity as registered in messages/interfaces.yaml. RPC keys carry
# the interface and version chunks, so these travel with every key we build.
INTERFACE = "network_ping_pong"
VERSION = "v1"
PROCEDURE = "ping_network"
SUBJECT = "network_status"


def _to_proto_timestamp(message_field, ns: int) -> None:
    message_field.FromNanoseconds(ns)


def _from_proto_timestamp(message_field) -> int:
    return message_field.ToNanoseconds()


def build_pong(request_bytes: bytes, received_at_ns: int) -> bytes:
    """Answer a ping. Pure so the responder's contract can be tested directly.

    The request is echoed back inside the pong: the pinger needs t1 to compute
    anything, and carrying it in the reply means the responder holds no state
    and several pings can be in flight at once.
    """
    _, _, content = keelson.uncover(request_bytes)
    ping = NetworkPing.FromString(content)

    pong = NetworkPong()
    pong.ping.CopyFrom(ping)
    _to_proto_timestamp(pong.ping_received_at, received_at_ns)
    # Stamped as late as possible so the responder's own processing time lands
    # inside (t3 - t2) and is subtracted out of the RTT rather than inflating it.
    _to_proto_timestamp(pong.sent_at, time.time_ns())
    return keelson.enclose(pong.SerializeToString())


def build_status(
    reply_bytes: bytes,
    pong_received_at_ns: int,
    ping_host: str,
    pong_host: str,
) -> NetworkStatus:
    """Turn one reply into a NetworkStatus. Pure, for the same reason as above."""
    _, _, content = keelson.uncover(reply_bytes)
    pong = NetworkPong.FromString(content)

    result = compute(
        ping_sent_ns=_from_proto_timestamp(pong.ping.sent_at),
        ping_received_ns=_from_proto_timestamp(pong.ping_received_at),
        pong_sent_ns=_from_proto_timestamp(pong.sent_at),
        pong_received_ns=pong_received_at_ns,
        payload_size_bytes=len(pong.ping.payload),
    )

    status = NetworkStatus()
    status.ping_sent_at.FromNanoseconds(_from_proto_timestamp(pong.ping.sent_at))
    status.pong_sent_at.FromNanoseconds(_from_proto_timestamp(pong.sent_at))
    status.ping_host = ping_host
    status.pong_host = pong_host
    status.payload_size_mb = result.payload_size_mb
    status.round_trip_time_ms = result.round_trip_time_ms
    status.latency_ms = result.latency_ms
    status.clock_skew_ms = result.clock_skew_ms
    return status


def _make_responder(source_id: str):
    def _on_query(query) -> None:
        # Stamped first thing, before any decoding, so decode cost counts as
        # responder processing and is subtracted from the RTT.
        received_at_ns = time.time_ns()
        try:
            payload = query.payload
            if payload is None:
                logger.warning("ping_network query carried no payload; ignoring")
                return
            reply = build_pong(bytes(payload), received_at_ns)
            query.reply(query.key_expr, reply)
        except Exception:
            logger.exception("Failed to answer ping_network query")

    return _on_query


def ping_peer(
    session,
    realm: str,
    peer: str,
    self_entity: str,
    payload_bytes: int,
    timeout_s: float,
) -> Iterable[NetworkStatus]:
    """Ping one peer and yield a NetworkStatus per responder that answered.

    The responder id is a wildcard: a peer may run the manager under any source
    id, and requiring us to know it in advance would make configuration a
    guessing game. Several responders simply produce several links.

    `**`, not `*`: source ids are genuinely multi-segment on this network
    ("srv-herakles/sjofartsverket", "ins/3/sbg"), and a single-star wildcard
    silently matches none of them.
    """
    key = keelson.construct_rpc_key(realm, peer, INTERFACE, VERSION, PROCEDURE, "**")

    ping = NetworkPing()
    ping.payload = b"\0" * payload_bytes
    _to_proto_timestamp(ping.sent_at, time.time_ns())
    request = keelson.enclose(ping.SerializeToString())

    replies = []
    session.get(
        key, lambda reply: replies.append((time.time_ns(), reply)), payload=request
    )

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.02)

    for received_at_ns, reply in replies:
        try:
            ok = reply.ok
        except Exception:
            ok = None
        if ok is None:
            logger.warning("Peer %s replied with an error to ping_network", peer)
            continue
        try:
            yield build_status(bytes(ok.payload), received_at_ns, self_entity, peer)
        except Exception:
            logger.exception("Could not read pong from %s", peer)

    if not replies:
        # Silence is the signal: publishing a zero would read as a perfect link.
        logger.info("Peer %s did not answer ping_network within %.1fs", peer, timeout_s)


def run(session, args: argparse.Namespace) -> None:
    responder_key = keelson.construct_rpc_key(
        args.realm, args.entity_id, INTERFACE, VERSION, PROCEDURE, args.source_id
    )
    queryable = session.declare_queryable(
        responder_key, _make_responder(args.source_id)
    )
    logger.info("Declared queryable: %s", responder_key)

    publish_key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, SUBJECT, args.source_id
    )
    publisher = session.declare_publisher(publish_key)
    logger.info("Declared publisher: %s", publish_key)

    peers = [p.strip() for p in (args.peers or "").split(",") if p.strip()]
    if not peers:
        logger.info("No --peers configured; answering pings only.")

    try:
        while True:
            for peer in peers:
                for status in ping_peer(
                    session,
                    args.realm,
                    peer,
                    args.entity_id,
                    args.payload_bytes,
                    args.timeout,
                ):
                    publisher.put(keelson.enclose(status.SerializeToString()))
                    logger.info(
                        "%s -> %s  rtt=%.2f ms  latency=%.2f ms  skew=%.2f ms",
                        status.ping_host,
                        status.pong_host,
                        status.round_trip_time_ms,
                        status.latency_ms,
                        status.clock_skew_ms,
                    )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Shutting down.")
    finally:
        queryable.undeclare()
        publisher.undeclare()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="network_manager2keelson",
        description="Measure link quality between keelson entities.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, default="network")
    parser.add_argument(
        "--peers",
        type=str,
        default="",
        help="Comma-separated entity ids to ping. Empty = answer pings only.",
    )
    parser.add_argument(
        "--interval", type=float, default=10.0, help="Seconds between rounds"
    )
    parser.add_argument(
        "--timeout", type=float, default=2.0, help="Seconds to wait for replies"
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=0,
        help="Padding added to each ping, for measuring under load",
    )
    parser.add_argument("--mode", "-m", choices=["peer", "client"], default="peer")
    parser.add_argument("--connect", type=str, action="append", default=None)
    parser.add_argument("--log-level", type=int, default=logging.INFO)
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )

    conf = zenoh.Config()
    conf.insert_json5("mode", json.dumps(args.mode))
    if args.connect:
        conf.insert_json5("connect/endpoints", json.dumps(args.connect))

    with zenoh.open(conf) as session:
        logger.info("Zenoh session established")
        run(session, args)


if __name__ == "__main__":
    main()
