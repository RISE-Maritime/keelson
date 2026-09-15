#!/usr/bin/env python3
"""Warrant-propagating aggregator: a Layer 3 aggregator that derives the
authority level from the set of claims that remain licensed, instead of
from a score.

Consumes entity_health output and publishes two things under its own
source_id: its determination as OperationalAuthority on
operational_authority (level, reason, policy identity and constraints;
no scores), and its record as WarrantRecord on warrant_record (standing
transitions as they happen, periodic snapshots). Non-compensatory by
construction: a required ground that fails withdraws its dependents, and
healthy components elsewhere cannot offset it.

Clock discipline: evaluations are stamped with the evidence envelope's
enclosed_at, so a replayed recording reproduces the live run's record
stream byte for byte. Wall clock enters in exactly one place, and only in
the default --clock hybrid mode: between messages, ticks advance the
evaluation clock by the wall time elapsed since the last message (on the
enclosed_at axis), which is what detects evidence going stale. With
--clock data the engine advances only on message timestamps: gaps
register when the next message arrives, which is the correct semantics
for replay and makes two runs over the same input byte-identical.

If publishing the record stream fails, the engine rolls the transition
back (state never runs ahead of the record) and this process halts
loudly: a determination without a record is the one output this
connector must never produce.

Reconfigurable at runtime over the Configurable RPC interface
(get_config / set_config): the claim graph is replaced under one lock,
every claim restarts WITHDRAWN, and the next snapshot carries the new
policy_config_digest. A rejected document changes nothing.
"""

import argparse
import logging
import threading
import time
from pathlib import Path

import keelson
import zenoh
from keelson.payloads.EntityHealth_pb2 import EntityHealth
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    make_configurable,
)
from warrant_aggregator.model import ClaimGraph
from warrant_aggregator.records import JsonlWriter
from warrant_aggregator.runtime import Runtime
from warrant_aggregator.wire import (
    policy_config_digest,
    validate_ladder_names,
    warrant_record_from_event,
)

logger = logging.getLogger("warrant-aggregator")

# configuration_json is where make_configurable republishes every applied
# configuration; the configurable/v1 interface token is declared inside it.
SUBJECTS = ["operational_authority", "warrant_record", "configuration_json"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--realm", required=True)
    parser.add_argument("--entity-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--config", required=True, type=Path, help="Claim graph YAML")
    parser.add_argument(
        "--publish-rate-hz",
        type=float,
        default=1.0,
        help="Rate of OperationalAuthority publications (level changes "
        "publish immediately regardless)",
    )
    parser.add_argument(
        "--policy-id",
        default="warrant_graph/v1",
        help="Policy identity stamped on every determination",
    )
    parser.add_argument(
        "--clock",
        choices=["hybrid", "data"],
        default="hybrid",
        help="hybrid: ticks advance the clock by wall time since the last "
        "message, detecting staleness live. data: the clock advances only "
        "on message timestamps (replay, deterministic verification)",
    )
    parser.add_argument(
        "--records-jsonl",
        type=Path,
        default=None,
        help="Also append engine events to this JSONL file (debug "
        "convenience; the wire is the record)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level)

    graph = ClaimGraph.load(args.config)
    validate_ladder_names(graph)
    digest = policy_config_digest(args.config)

    jsonl = None
    if args.records_jsonl:
        jsonl = JsonlWriter(
            args.records_jsonl,
            {
                "t_ns": time.time_ns(),
                "start_ns": time.time_ns(),
                "graph": str(args.config),
            },
        )

    halt = threading.Event()

    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )
    with zenoh.open(zconf) as session:
        record_publisher = declare_publisher(
            session,
            keelson.construct_pubsub_key(
                args.realm, args.entity_id, "warrant_record", args.source_id
            ),
        )
        authority_publisher = declare_publisher(
            session,
            keelson.construct_pubsub_key(
                args.realm, args.entity_id, "operational_authority", args.source_id
            ),
        )

        def record_sink(event: dict) -> None:
            record = warrant_record_from_event(event)
            if record is not None:
                record_publisher.put(keelson.enclose(record.SerializeToString()))
            if jsonl is not None:
                jsonl.write(event)

        def authority_sink(msg) -> None:
            authority_publisher.put(keelson.enclose(msg.SerializeToString()))

        runtime = Runtime(
            graph,
            record_sink,
            authority_sink,
            policy_id=args.policy_id,
            clock=args.clock,
            digest=digest,
        )

        def on_sample(sample: zenoh.Sample) -> None:
            try:
                _received, enclosed_at, payload = keelson.uncover(
                    sample.payload.to_bytes()
                )
                msg = EntityHealth()
                msg.ParseFromString(payload)
            except Exception:
                logger.exception("Failed to decode entity_health sample")
                return
            try:
                # Evaluations are stamped with the evidence's own timestamp,
                # so live and replay agree by construction.
                runtime.feed(enclosed_at, msg)
            except Exception:
                logger.critical(
                    "Record stream publication failed; halting: a "
                    "determination without a record must not be produced",
                    exc_info=True,
                )
                halt.set()

        def set_config(new_spec) -> None:
            # A ValueError is a rejected document and becomes the reply; any
            # other failure happened while publishing the swapped state, and
            # the same rule as on_sample applies.
            try:
                runtime.set_config(new_spec)
            except ValueError:
                raise
            except Exception:
                logger.critical(
                    "Record stream publication failed during reconfiguration; "
                    "halting: a determination without a record must not be "
                    "produced",
                    exc_info=True,
                )
                halt.set()
                raise

        configurable = make_configurable(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            runtime.get_config,
            set_config,
        )

        with declare_liveliness(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            pubsub_subjects=SUBJECTS,
        ):
            session.declare_subscriber(
                f"{args.realm}/@v0/{args.entity_id}/pubsub/entity_health/**",
                on_sample,
            )
            logger.info(
                "Warrant aggregator running: policy_id=%s, %d claims",
                args.policy_id,
                len(graph.claims),
            )
            interval = 1.0 / args.publish_rate_hz
            try:
                while not halt.is_set():
                    time.sleep(interval)
                    # Wall clock's one entry point is Runtime.now_ns: how long
                    # since the last message, on the message timestamp axis,
                    # so staleness is detectable without forking the clock.
                    runtime.tick_and_publish()
                if halt.is_set():
                    raise SystemExit(1)
            except KeyboardInterrupt:
                logger.info("Shutting down")
            finally:
                del configurable
                if jsonl is not None:
                    jsonl.close()


if __name__ == "__main__":
    main()
