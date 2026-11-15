#!/usr/bin/env python3
"""Keelson network manager: answers ping_network, pings peers, publishes link quality.

Run one per site. Each instance plays both roles, which is what makes a link
measurable at all — a ping needs a responder at the far end, so a
responder-only deployment can never measure anything itself.

    network_manager2keelson --realm rise --entity-id ted \
        --source-id network --peers masslab,sf18

    declares   rise/@v0/ted/@rpc/network_ping_pong/v1/ping_network/network
    pings      rise/@v0/{peer}/@rpc/network_ping_pong/v1/ping_network/**
    publishes  rise/@v0/ted/pubsub/network_status/network
               rise/@v0/ted/pubsub/network_ping_loss_pct/network/{peer}
               rise/@v0/ted/pubsub/network_round_trip_time_jitter_ms/network/{peer}/{responder}
               rise/@v0/ted/pubsub/network_round_trip_time_p95_ms/network/{peer}/{responder}

    liveliness rise/@v0/ted/*/network
               rise/@v0/ted/pubsub/{each of the four subjects}/network
               rise/@v0/ted/@rpc/network_ping_pong/v1/*/network

`network_status` (`keelson.NetworkStatus`) is one exchange: published per
answer, carrying both ends of the link in its payload — so its key stays plain
rather than using `@target/{peer}`. A wildcard cannot cross an `@`-segment, so
a plain key lets a consumer collect every link with one subscription.

The other three are the link over time (`keelson.TimestampedFloatPeriod`, the
window actually covered in `period`), and they go out EVERY round, answered or
not — so a link that drops completely reads as loss rising to 100 %, live,
rather than as the same silence a quiet link gives. Loss is per peer (one ping
is one round); jitter and p95 are per responder, the link being the path to
one responder under the peer, keyed `{peer}/{responder}` (spec §2.1.2).

This replaces keelson-processor-network-manager, which spoke the pre-`@v0` key
format and imported a payload type (`payloads/NetworkPing`) that keelson has
since deleted. Living in the monorepo is the point: it can no longer drift away
from the definitions it depends on.
"""

import argparse
import logging
import time
from typing import Dict, Iterable, Tuple

import zenoh

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.NetworkPingPong_pb2 import NetworkPing, NetworkPong
from keelson.payloads.NetworkStatus_pb2 import NetworkStatus
from keelson.payloads.Primitives_pb2 import TimestampedFloatPeriod
from keelson.scaffolding import (
    RpcOp,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    serve_rpc,
)

from network_manager.pingpong import PeerWindow, ResponderWindow, compute

logger = logging.getLogger("network_manager")

# Interface identity as registered in messages/interfaces.yaml. RPC keys carry
# the interface and version chunks, so these travel with every key we build.
INTERFACE = "network_ping_pong"
VERSION = "v1"
PROCEDURE = "ping_network"
SUBJECT = "network_status"
LOSS_SUBJECT = "network_ping_loss_pct"
JITTER_SUBJECT = "network_round_trip_time_jitter_ms"
P95_SUBJECT = "network_round_trip_time_p95_ms"
SUBJECTS = [SUBJECT, LOSS_SUBJECT, JITTER_SUBJECT, P95_SUBJECT]


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


def build_period(value: float, end_ns: int, period_s: float) -> bytes:
    """An enclosed TimestampedFloatPeriod: `value` over the `period_s` seconds
    ending at `end_ns`. Pure, like build_status."""
    msg = TimestampedFloatPeriod()
    msg.timestamp.FromNanoseconds(end_ns)
    msg.value = value
    msg.period.FromNanoseconds(int(period_s * 1e9))
    return keelson.enclose(msg.SerializeToString())


def responder_of(reply_key: str, realm: str, peer: str) -> str:
    """The responder's source id from the key it answered on — every chunk of
    it, since source ids here are genuinely multi-segment."""
    prefix = keelson.construct_rpc_key(realm, peer, INTERFACE, VERSION, PROCEDURE, "")
    if not reply_key.startswith(prefix):
        raise ValueError(f"reply key {reply_key!r} is not under {prefix!r}")
    return reply_key[len(prefix) :]


def _handle_ping(op: RpcOp) -> None:
    # Stamped first thing, before any decoding, so decode cost counts as
    # responder processing and is subtracted from the RTT.
    received_at_ns = time.time_ns()
    if not op.request_bytes:
        op.reply_err(
            "ping_network query carried no payload; expected a NetworkPing",
            ErrorResponse.Code.INVALID_ARGUMENT,
        )
        return
    op.reply_ok(build_pong(op.request_bytes, received_at_ns))


def serve(session, realm: str, entity_id: str, source_id: str):
    """Declare the responder and its interface-level liveliness token."""
    return serve_rpc(
        session,
        base_path=realm,
        entity_id=entity_id,
        responder_id=source_id,
        interface=INTERFACE,
        version=VERSION,
        handlers={PROCEDURE: _handle_ping},
        log=logger,
    )


def ping_peer(
    session,
    realm: str,
    peer: str,
    self_entity: str,
    payload_bytes: int,
    timeout_s: float,
) -> Iterable[Tuple[str, NetworkStatus]]:
    """Ping one peer and yield (responder id, NetworkStatus) per responder that
    answered.

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
            responder = responder_of(str(ok.key_expr), realm, peer)
            yield responder, build_status(
                bytes(ok.payload), received_at_ns, self_entity, peer
            )
        except Exception:
            logger.exception("Could not read pong from %s", peer)

    if not replies:
        # Silence is the signal: publishing a zero would read as a perfect link.
        logger.info("Peer %s did not answer ping_network within %.1fs", peer, timeout_s)


def run(session, args: argparse.Namespace) -> None:
    # serve_rpc declares the queryable and then the interface-level token
    # (queryable first, so presence implies the ability to receive — spec §5).
    serve(session, args.realm, args.entity_id, args.source_id)

    peers = [p.strip() for p in (args.peers or "").split(",") if p.strip()]
    if not peers:
        logger.info("No --peers configured; answering pings only.")

    # Source-level and per-subject tokens. The token declares capability,
    # not activity: it stays up while a link is silent, which is exactly what
    # lets a consumer tell "the manager is here but the peer never answered"
    # from "there is no manager" (#234). The windowed subjects carry the peer
    # (and responder) after the source id, so their tokens end at the source
    # id, the producer prefix (spec §5.2).
    with declare_liveliness(
        session, args.realm, args.entity_id, args.source_id, pubsub_subjects=SUBJECTS
    ):
        _measure_forever(session, args, peers)


def _measure_forever(session, args: argparse.Namespace, peers) -> None:
    # One PeerWindow per peer (loss: one ping is one round, answered if ANY
    # responder under the peer answered) and one ResponderWindow per link
    # (jitter, p95), each timed by its own reply.
    peer_windows = {peer: PeerWindow(window_s=args.window) for peer in peers}
    links: Dict[Tuple[str, str], ResponderWindow] = {}
    publishers: Dict[str, object] = {}

    def put(subject: str, source_id: str, payload: bytes) -> None:
        key = keelson.construct_pubsub_key(
            args.realm, args.entity_id, subject, source_id
        )
        if key not in publishers:
            publishers[key] = declare_publisher(session, key)
            logger.info("Declared publisher: %s", key)
        publishers[key].put(payload)

    try:
        while True:
            for peer in peers:
                started_s = time.time()
                answers = list(
                    ping_peer(
                        session,
                        args.realm,
                        peer,
                        args.entity_id,
                        args.payload_bytes,
                        args.timeout,
                    )
                )
                end_s = time.time()
                _publish_round(
                    put,
                    args,
                    peer,
                    peer_windows[peer],
                    links,
                    answers,
                    started_s,
                    end_s,
                )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Shutting down.")
    finally:
        for p in publishers.values():
            p.undeclare()


def _publish_round(put, args, peer, peer_window, links, answers, started_s, end_s):
    """Publish one round to one peer: a status per answer, then the windowed
    figures — loss always, jitter and p95 for each link with two answers."""
    end_ns = int(end_s * 1e9)
    peer_window.record(started_s, bool(answers), end_s=end_s)
    for responder, status in answers:
        put(SUBJECT, args.source_id, keelson.enclose(status.SerializeToString()))
        links.setdefault((peer, responder), ResponderWindow(window_s=args.window))
        links[(peer, responder)].record(
            started_s, status.round_trip_time_ms, end_s=end_s
        )
        logger.info(
            "%s -> %s (%s)  rtt=%.2f ms  latency=%.2f ms  skew=%.2f ms",
            status.ping_host,
            status.pong_host,
            responder,
            status.round_trip_time_ms,
            status.latency_ms,
            status.clock_skew_ms,
        )

    span_s = peer_window.span_s(end_s)
    loss = peer_window.loss_pct()
    if loss is not None:
        put(
            LOSS_SUBJECT, f"{args.source_id}/{peer}", build_period(loss, end_ns, span_s)
        )
        logger.info("%s loss=%.0f%% over %.0fs", peer, loss, span_s)

    for (link_peer, responder), window in list(links.items()):
        if link_peer != peer:
            continue
        window.age(end_s)
        if not len(window):
            del links[(link_peer, responder)]  # gone quiet for a whole window
            continue
        jitter, p95 = window.jitter_ms(), window.p95_ms()
        if jitter is None or p95 is None:
            continue
        link = f"{args.source_id}/{peer}/{responder}"
        put(JITTER_SUBJECT, link, build_period(jitter, end_ns, span_s))
        put(P95_SUBJECT, link, build_period(p95, end_ns, span_s))


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
        "--window",
        type=float,
        default=60.0,
        help="Seconds of rounds that loss, jitter and p95 are taken over",
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=0,
        help="Padding added to each ping, for measuring under load",
    )
    add_common_arguments(parser)
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )

    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    with zenoh.open(conf) as session:
        logger.info("Zenoh session established")
        run(session, args)


if __name__ == "__main__":
    main()
