"""Whether this vessel will confirm a watch handover, and why.

Pure: no zenoh, no I/O, no clock. The connector binary does the wire; this
decides. Kept apart so the rule can be tested without a bus, the way
`composite_aggregator.authority` is.

THE RULE. A handover asks the vessel whether it is willing to be operated
remotely by a new watch. The vessel already answers that question continuously,
on `operational_authority` — "the vessel's own view of how much operating
authority it can currently justify ... this is the vessel's veto on accepting
remote control, and it is therefore computed on the vessel". So this does not
invent a second opinion; it reads the standing one and compares it to a floor.

Two MUSTs from OperationalAuthority.proto are load-bearing here:

  - "Consumers deciding whether to grant control MUST read `level` (or
    authority_score), never composite_score."  A vessel can be 92% healthy and
    still have authority 0.0 because one essential component is down. Reading
    the aggregate would confirm a handover the vessel is refusing.
  - AUTHORITY_LEVEL_UNKNOWN "MUST be treated as non-authorizing, exactly like
    MINIMAL_SAFE_MODE, and MUST NOT be treated as 'no constraints known'." It
    means the monitor could not tell, which is not a yes.

Silence is likewise not a yes: no authority on the wire at all is refused, with
that stated as the reason.
"""

# From OperationalAuthority.AuthorityLevel. Mirrored rather than imported so this
# module stays importable without the generated protobuf, and so a rename
# upstream shows up as a test failure here rather than a silent renumber.
LEVEL_NAMES = {
    0: "UNKNOWN",
    1: "MINIMAL_SAFE_MODE",
    2: "SUPERVISED_REMOTE",
    3: "REMOTE_CONTROLLED",
    4: "ASSISTED_AUTONOMOUS",
    5: "FULL_AUTONOMOUS",
}

#: Anything at or below this is non-authorizing whatever the floor is set to.
#: UNKNOWN(0) and MINIMAL_SAFE_MODE(1) both mean "not available for remote work".
NON_AUTHORIZING = {0, 1}

DEFAULT_MIN_LEVEL = 2  # SUPERVISED_REMOTE

CAUSE_NAMES = {
    0: "UNSPECIFIED",
    1: "DEGRADED",
    2: "CRITICAL",
    3: "INACTIVE",
    4: "UNKNOWN",
    5: "NOT_ADVERTISED",
    6: "CONFIGURATION_INVALID",
}


def level_name(level):
    """Human name for a level, including one this build does not know."""
    return LEVEL_NAMES.get(level, f"LEVEL_{level}")


def constraints_of(authority):
    """`active_constraints` as plain dicts — the vessel's own account of its ceilings."""
    out = []
    for c in getattr(authority, "active_constraints", []) or []:
        out.append(
            {
                "componentId": c.component_id,
                "capLevel": c.cap_level,
                "capLevelName": level_name(c.cap_level),
                "cause": CAUSE_NAMES.get(c.cause, str(c.cause)),
                "subjectId": c.subject_id if c.HasField("subject_id") else None,
            }
        )
    return out


def decide(authority, min_level=DEFAULT_MIN_LEVEL):
    """Confirm or refuse.

    :param authority: the latest OperationalAuthority for this entity, or None
        when nothing has been heard.
    :returns: ``(confirmed: bool, verdict: dict)``. The verdict travels on the
        record either way — a confirmation that records *why* the vessel was
        willing is as much of an audit trail as a refusal.
    """
    if authority is None:
        return False, {
            "level": 0,
            "levelName": level_name(0),
            "reason": (
                "No operational_authority on the wire for this entity — the vessel "
                "cannot say whether it accepts remote operation, which is not a yes."
            ),
            "constraints": [],
            "policyId": None,
        }

    level = int(getattr(authority, "level", 0) or 0)
    verdict = {
        "level": level,
        "levelName": level_name(level),
        "constraints": constraints_of(authority),
        "policyId": authority.policy_id if authority.HasField("policy_id") else None,
    }

    if level in NON_AUTHORIZING:
        verdict["reason"] = (
            f"Vessel authority is {level_name(level)} — it is not accepting remote "
            f"operation at all."
        )
        return False, verdict

    if level < min_level:
        verdict["reason"] = (
            f"Vessel authority is {level_name(level)}, below the {level_name(min_level)} "
            f"floor this connector is configured with."
        )
        return False, verdict

    # `reason` on the message is explicitly operators-and-logs only ("consumers
    # MUST NOT parse this"), so it is carried for a human to read, never matched on.
    verdict["reason"] = f"Vessel authority is {level_name(level)}."
    return True, verdict
