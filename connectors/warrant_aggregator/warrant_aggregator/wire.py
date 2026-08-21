"""Between engine events and the bus.

The warrant_aggregator publishes two things. Its record: WarrantRecord
messages on the warrant_record subject, standing transitions as they
happen and periodic snapshots. Its determination: OperationalAuthority on
the operational_authority subject under its own source_id, level and
reason and policy identity only — this policy derives the level from
claim standings, not from a score, so it publishes neither
composite_score nor authority_score, and withdrawn claims appear as
active_constraints.
"""

import hashlib

from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.payloads.WarrantRecord_pb2 import WarrantRecord

_STANDING_TO_PROTO = {
    "WITHDRAWN": WarrantRecord.Standing.STANDING_WITHDRAWN,
    "WEAKENED": WarrantRecord.Standing.STANDING_WEAKENED,
    "LICENSED": WarrantRecord.Standing.STANDING_LICENSED,
}
_PROTO_TO_STANDING = {v: k for k, v in _STANDING_TO_PROTO.items()}

_EVIDENCE_LEVEL_TO_CAUSE = {
    "": OperationalAuthority.AuthorityConstraint.Cause.CAUSE_UNKNOWN,
    "UNKNOWN": OperationalAuthority.AuthorityConstraint.Cause.CAUSE_UNKNOWN,
    "INACTIVE": OperationalAuthority.AuthorityConstraint.Cause.CAUSE_INACTIVE,
    "CRITICAL": OperationalAuthority.AuthorityConstraint.Cause.CAUSE_CRITICAL,
    "DEGRADED": OperationalAuthority.AuthorityConstraint.Cause.CAUSE_DEGRADED,
    "NOT_ADVERTISED": (
        OperationalAuthority.AuthorityConstraint.Cause.CAUSE_NOT_ADVERTISED
    ),
}


def policy_config_digest(graph_path) -> bytes:
    with open(graph_path, "rb") as f:
        return hashlib.sha256(f.read()).digest()


def validate_ladder_names(graph):
    """The ladder must speak AuthorityLevel so the determination can be
    published as OperationalAuthority."""
    valid = set(OperationalAuthority.AuthorityLevel.keys())
    for rung in graph.ladder:
        if f"AUTHORITY_LEVEL_{rung.name}" not in valid:
            raise ValueError(
                f"ladder rung {rung.name} is not an AuthorityLevel "
                f"(expected one of {sorted(v.removeprefix('AUTHORITY_LEVEL_') for v in valid)})"
            )


def _fill_rebuttals(container, fired):
    for rebuttal in fired:
        entry = container.add()
        entry.id = rebuttal["id"]
        entry.description = rebuttal["description"]
        entry.evidence = rebuttal["evidence"]
        entry.evidence_level = rebuttal.get("evidence_level", "")


def _fill_grounds(container, grounds):
    for claim_id, standing in grounds.items():
        entry = container.add()
        entry.claim_id = claim_id
        entry.standing = _STANDING_TO_PROTO[standing]


def warrant_record_from_event(event: dict) -> WarrantRecord | None:
    """Engine event -> WarrantRecord, None for kinds that do not ride the
    record stream (the level lives on operational_authority)."""
    record = WarrantRecord()
    record.timestamp.FromNanoseconds(event["t_ns"])
    if event["kind"] == "standing":
        transition = record.standing_transition
        transition.claim_id = event["claim"]
        transition.from_standing = _STANDING_TO_PROTO[event["from"]]
        transition.to_standing = _STANDING_TO_PROTO[event["to"]]
        _fill_rebuttals(transition.rebuttals_fired, event["rebuttals_fired"])
        _fill_grounds(transition.grounds, event["grounds"])
        return record
    if event["kind"] == "snapshot":
        snapshot = record.snapshot
        for name, claim in event["claims"].items():
            state = snapshot.claims.add()
            state.claim_id = name
            state.standing = _STANDING_TO_PROTO[claim["standing"]]
            state.since.FromNanoseconds(claim["since_ns"])
            _fill_rebuttals(state.rebuttals_fired, claim["rebuttals_fired"])
            _fill_grounds(state.grounds, claim["grounds"])
            if claim.get("target"):
                state.target_standing = _STANDING_TO_PROTO[claim["target"]]
            state.statement = claim.get("statement", "")
            state.warrant = claim.get("warrant", "")
            state.backing = claim.get("backing", "")
        if event.get("policy_config_digest"):
            # Carried hex-encoded in engine events so the JSONL debug sink
            # stays plain JSON.
            snapshot.policy_config_digest = bytes.fromhex(event["policy_config_digest"])
        if event.get("policy_id"):
            snapshot.policy_id = event["policy_id"]
        return record
    return None


def event_from_warrant_record(record: WarrantRecord) -> dict:
    """WarrantRecord -> engine-shaped event dict, for reconstruction."""
    t_ns = record.timestamp.ToNanoseconds()
    kind = record.WhichOneof("event")
    if kind == "standing_transition":
        transition = record.standing_transition
        return {
            "kind": "standing",
            "t_ns": t_ns,
            "claim": transition.claim_id,
            "from": _PROTO_TO_STANDING[transition.from_standing],
            "to": _PROTO_TO_STANDING[transition.to_standing],
            "rebuttals_fired": [
                {
                    "id": r.id,
                    "description": r.description,
                    "evidence": r.evidence,
                    "evidence_level": r.evidence_level,
                }
                for r in transition.rebuttals_fired
            ],
            "grounds": {
                g.claim_id: _PROTO_TO_STANDING[g.standing] for g in transition.grounds
            },
        }
    if kind == "snapshot":
        return {
            "kind": "snapshot",
            "t_ns": t_ns,
            "level": None,  # the level lives on operational_authority
            "claims": {
                state.claim_id: {
                    "standing": _PROTO_TO_STANDING[state.standing],
                    # Absent on records written before the field existed.
                    "target": _PROTO_TO_STANDING.get(state.target_standing),
                    "since_ns": state.since.ToNanoseconds(),
                    "rebuttals_fired": [
                        {
                            "id": r.id,
                            "description": r.description,
                            "evidence": r.evidence,
                            "evidence_level": r.evidence_level,
                        }
                        for r in state.rebuttals_fired
                    ],
                    "grounds": {
                        g.claim_id: _PROTO_TO_STANDING[g.standing]
                        for g in state.grounds
                    },
                    "statement": state.statement,
                    "warrant": state.warrant,
                    "backing": state.backing,
                }
                for state in snapshot_claims(record)
            },
        }
    raise ValueError(f"WarrantRecord with no event set at {t_ns}")


def snapshot_claims(record: WarrantRecord):
    return record.snapshot.claims


def operational_authority_from_state(
    engine, t_ns: int, policy_id: str, digest: bytes
) -> OperationalAuthority:
    msg = OperationalAuthority()
    msg.timestamp.FromNanoseconds(t_ns)
    msg.level = OperationalAuthority.AuthorityLevel.Value(
        f"AUTHORITY_LEVEL_{engine.level}"
    )
    withdrawn = [
        name
        for name, state in engine.states.items()
        if state.standing == 0  # WITHDRAWN
    ]
    msg.reason = (
        "all claims licensed"
        if not withdrawn
        else "withdrawn: " + ", ".join(sorted(withdrawn))
    )
    msg.policy_id = policy_id
    msg.policy_config_digest = digest
    # No composite_score, no authority_score: this policy derives the level
    # from claim standings, not from a score.
    for name in sorted(withdrawn):
        state = engine.states[name]
        constraint = msg.active_constraints.add()
        constraint.component_id = name
        constraint.cap_level = msg.level
        cause = OperationalAuthority.AuthorityConstraint.Cause.CAUSE_UNSPECIFIED
        if state.fired:
            levels = {r.get("evidence_level", "") for r in state.fired}
            causes = {_EVIDENCE_LEVEL_TO_CAUSE.get(lv) for lv in levels}
            causes.discard(None)
            if len(causes) == 1:
                cause = causes.pop()
        constraint.cause = cause
    return msg
