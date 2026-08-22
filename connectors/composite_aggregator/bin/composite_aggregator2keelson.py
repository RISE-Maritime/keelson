#!/usr/bin/env python3
"""Compensatory composite: a Layer 3 aggregation policy over EntityHealth.

Consumes `entity_health` and publishes `operational_authority` under its own
source_id: a mean of per-source health, each source discounted by how much of
it could be assessed, with essential requirements imposing ceilings the mean
cannot buy back. Beside it, `warrant_aggregator` derives the same subject from
claim standings instead of a score. Two policies, one wire, self-identified by
`policy_id` and allowed to disagree — see docs/health-monitoring.md.

This connector used to be the second half of `entity_health`. It reads the
`EntityHealth` message rather than that connector's internals, so it is a
peer of `warrant_aggregator` rather than a privileged one, and a deployment
may run either, both, or neither.
"""

import argparse
import hashlib
import json
import logging
import pathlib
import threading
import time

import zenoh

import keelson
from keelson.payloads.EntityHealth_pb2 import EntityHealth
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
)

from composite_aggregator.authority import (
    ASSESSED_LEVELS,
    Policy,
    evaluate_authority,
    level_for,
)
from composite_aggregator.levels import HEALTH_NOT_ADVERTISED, HEALTH_UNKNOWN

logger = logging.getLogger("composite-aggregator")

SUBJECTS = ["operational_authority"]


def essential_requirements(policy_config: dict) -> set:
    """The configured safety prerequisites, as `(source, subject_or_None)`.

    A entry naming only a source is the whole-source shorthand, for a source
    that genuinely is one indivisible requirement; naming a subject is the
    precise form, and the reason it exists is that a GNSS source's position
    fix can be a hard prerequisite while its four ancillary diagnostics are
    not. Capping on the whole source there would let an unread diagnostic veto
    the vessel. Marking the source does not imply its subjects and marking a
    subject does not imply its source — they are different claims, and
    `evaluate_constraints` caps each on its own terms.

    This is policy, not evidence: which components a vessel may not operate
    without is a statement about the mission, and it belongs with the ladder
    rather than with the watch config that decides health levels.
    """
    out = set()
    for req in policy_config.get("essential", []):
        out.add((req["source"], req.get("subject")))
    return out


def _build_operational_authority(
    authority, sources, timestamp_ns: int, essential=frozenset(), policy=None
) -> OperationalAuthority:
    msg = OperationalAuthority()
    msg.timestamp.FromNanoseconds(timestamp_ns)
    msg.level = authority.level
    msg.composite_score = authority.composite_score
    msg.reason = authority.reason
    for name, score in authority.component_scores.items():
        msg.component_scores[name] = score

    # Left unset when no valid determination could be made; 0.0 is an ordinary
    # "all stop" and a consumer has to be able to tell the two apart.
    if authority.authority_score is not None:
        msg.authority_score = authority.authority_score

    subjects_by_source = {s.name: getattr(s, "subjects", []) or [] for s in sources}
    # From the CONFIG requirement set, not from authority.constraints: the
    # proto field means "this source carries an essential requirement", which
    # is config truth and holds on every tick. Constraints are only emitted
    # while a requirement is capping or invalid, so deriving the flag from
    # them made a healthy essential source read `essential: false` — flipping
    # to true exactly when it failed, which is the opposite of a stable fact.
    essential_sources = {req[0] for req in essential}
    caps_by_source = {c.component: c.cap_score for c in authority.constraints}

    for a in authority.assessments:
        entry = msg.source_assessments.add()
        entry.source_id = a.name
        entry.health_score = a.health_score
        entry.coverage_fraction = a.coverage_fraction
        entry.effective_score = a.effective_score
        entry.essential = a.name in essential_sources
        if a.name in caps_by_source:
            entry.authority_cap = caps_by_source[a.name]

        subjects = subjects_by_source.get(a.name, [])
        entry.eligible_subject_count = sum(
            1 for q in subjects if q.level != HEALTH_NOT_ADVERTISED
        )
        entry.assessed_subject_count = sum(
            1 for q in subjects if q.level in ASSESSED_LEVELS
        )
        entry.unassessed_subjects.extend(
            q.name for q in subjects if q.level == HEALTH_UNKNOWN
        )
        entry.not_advertised_subjects.extend(
            q.name for q in subjects if q.level == HEALTH_NOT_ADVERTISED
        )

    for c in authority.constraints:
        entry = msg.active_constraints.add()
        entry.component_id = c.component
        # No hysteresis on a ceiling: level_for() with no previous level is the
        # bare ladder, which is what a cap must be.
        entry.cap_level = level_for(c.cap_score, policy=policy)
        entry.cap_score = c.cap_score
        entry.cause = c.cause
        if c.subject is not None:
            entry.subject_id = c.subject

    return msg


def load_config(path: pathlib.Path) -> dict:
    with open(path) as f:
        return json.load(f)


def config_digest(path: pathlib.Path) -> bytes:
    """SHA-256 of the policy file as it sits on disk.

    Over the bytes rather than a canonical form of the parsed dict, so an
    auditor holding the file reproduces it with `sha256sum` and nothing else.
    That is only sound because this connector cannot be reconfigured at
    runtime: the file is the policy for the life of the process.
    """
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).digest()


def run(session: zenoh.Session, args: argparse.Namespace) -> None:
    config = load_config(args.config)
    policy = Policy.from_config(config)
    essential = essential_requirements(config)
    digest = config_digest(args.config)
    logger.info(
        "Composite aggregator running: policy_id=%s, %d essential requirement(s)",
        args.policy_id,
        len(essential),
    )

    publisher = declare_publisher(
        session,
        keelson.construct_pubsub_key(
            args.realm, args.entity_id, "operational_authority", args.source_id
        ),
    )

    lock = threading.Lock()
    # The only state the determination carries between ticks. Held here rather
    # than inside evaluate_authority() so that function stays pure: see its
    # docstring, and level_for()'s, for why the ladder is sticky.
    state = {"previous_level": None, "latest": None, "enclosed_at": None}

    def on_sample(sample: zenoh.Sample) -> None:
        try:
            _received, enclosed_at, payload = keelson.uncover(sample.payload.to_bytes())
            msg = EntityHealth()
            msg.ParseFromString(payload)
        except Exception:
            logger.exception("Failed to decode entity_health sample")
            return
        with lock:
            state["latest"], state["enclosed_at"] = msg, enclosed_at

    with declare_liveliness(
        session, args.realm, args.entity_id, args.source_id, pubsub_subjects=SUBJECTS
    ):
        session.declare_subscriber(
            f"{args.realm}/@v0/{args.entity_id}/pubsub/entity_health/**", on_sample
        )
        interval = 1.0 / max(args.publish_rate_hz, 0.01)
        while True:
            time.sleep(interval)
            with lock:
                health, enclosed_at = state["latest"], state["enclosed_at"]
                if health is None:
                    continue  # no evidence yet, nothing to determine
                # Stamped with the evidence's own timestamp, so a replayed
                # recording reproduces the live run's determination.
                authority = evaluate_authority(
                    health.sources,
                    state["previous_level"],
                    essential=essential,
                    policy=policy,
                )
                state["previous_level"] = authority.level
                msg = _build_operational_authority(
                    authority, health.sources, enclosed_at, essential, policy
                )
                msg.policy_id = args.policy_id
                msg.policy_config_digest = digest
                publisher.put(
                    keelson.enclose(msg.SerializeToString(), enclosed_at=enclosed_at)
                )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--realm", required=True)
    parser.add_argument("--entity-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--config", required=True, type=pathlib.Path, help="Composite policy JSON"
    )
    parser.add_argument("--publish-rate-hz", type=float, default=0.1)
    parser.add_argument(
        "--policy-id",
        default="composite/v1",
        help="Policy identity stamped on every determination",
    )
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level)

    zconf = create_zenoh_config(
        mode=args.mode, connect=args.connect, listen=args.listen
    )
    with zenoh.open(zconf) as session:
        try:
            run(session, args)
        except KeyboardInterrupt:
            logger.info("Shutting down")


if __name__ == "__main__":
    main()
