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
that stated as the reason. Nor is *stale* silence — see `GATE_STALE_AUTHORITY`.

WHAT A VERDICT HAS TO CARRY, and why it is more than the reason prose. The
verdict is the only durable record of a decision: it is stored on the handover
key and read back long afterwards to ask whether the configured floor is set
right. That question cannot be answered from `level` alone — you also need the
bar it was judged against, and *which* of the two gates fired, because they mean
opposite things. A `non_authorizing` refusal says nothing about the floor (0 and
1 refuse at every setting); a `below_floor` refusal is *entirely* about the
floor. So both `minLevel` and `gate` ride on every verdict, refusal or not.

`gate` is a stable token rather than something to be recovered from `reason`,
deliberately. OperationalAuthority.proto forbids parsing its own `reason`
("consumers MUST NOT parse this"), and the same discipline has to hold here or
every future count of refusals becomes a regex over English prose.
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

#: REMOTE_CONTROLLED. The lowest floor that decides anything.
#:
#: NON_AUTHORIZING makes the floor inert below 3: at 0, 1 or 2 the outcome is
#: identical for every possible level, because 0 and 1 refuse via
#: GATE_NON_AUTHORIZING and everything else clears the floor. So a default of 2
#: was not a lenient policy, it was NO policy — every refusal it could produce
#: was the protocol-mandated one, and the flag existed without ever being
#: consulted.
#:
#: 3 is the first setting at which this connector expresses a view of its own:
#: it refuses SUPERVISED_REMOTE, a vessel that is available but degraded. That
#: is a real cost and it falls on the outgoing operator, who is stranded on a
#: watch precisely when the vessel most needs one. It is the right default
#: anyway, because the alternative is a gate that reads as a safety control in
#: the config file and is not one. A deployment that wants the old behaviour
#: sets `--min-level 2` and that 2 is now a decision somebody took, visible in
#: the record as GATE_BELOW_FLOOR never firing.
DEFAULT_MIN_LEVEL = 3

#: Which test produced the verdict. Stable tokens — safe to count, unlike `reason`.
#:
#: `non_authorizing` and `below_floor` mean opposite things about the floor: the
#: first says nothing about it (0 and 1 refuse at every setting), the second is
#: entirely about it. Telling them apart in the record is what lets a deployment
#: ask, months later, whether its floor is set right.
#: Refuse rather than trust a reading older than this, in seconds.
#: Three publish periods at the composite aggregator's 0.1 Hz default, and thirty
#: at the 1 Hz a simulator rig runs — generous enough that a missed sample or two
#: never strands an operator, short enough that a dead aggregator is caught within
#: the time a handover takes to answer.
DEFAULT_MAX_AGE_S = 30.0

GATE_CONFIRMED = "confirmed"
GATE_NON_AUTHORIZING = "non_authorizing"
GATE_BELOW_FLOOR = "below_floor"
GATE_NO_AUTHORITY = "no_authority"
GATE_STALE_AUTHORITY = "stale_authority"

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


def decide(authority, min_level=DEFAULT_MIN_LEVEL, age_s=None, max_age_s=None):
    """Confirm or refuse.

    :param authority: the latest OperationalAuthority for this entity, or None
        when nothing has been heard.
    :param age_s: seconds since that reading arrived, or None if not tracked.
        The caller owns the clock so this module stays pure and testable.
    :param max_age_s: refuse rather than trust a reading older than this. None
        disables the check, which is the pre-existing behaviour.
    :returns: ``(confirmed: bool, verdict: dict)``. The verdict travels on the
        record either way — a confirmation that records *why* the vessel was
        willing is as much of an audit trail as a refusal.
    """
    base = {
        "minLevel": int(min_level),
        "minLevelName": level_name(int(min_level)),
        "constraints": [],
        "policyId": None,
    }

    if authority is None:
        return False, {
            **base,
            "level": 0,
            "levelName": level_name(0),
            "gate": GATE_NO_AUTHORITY,
            "reason": (
                "No operational_authority on the wire for this entity — the vessel "
                "cannot say whether it accepts remote operation, which is not a yes."
            ),
        }

    level = int(getattr(authority, "level", 0) or 0)
    verdict = {
        **base,
        "level": level,
        "levelName": level_name(level),
        "constraints": constraints_of(authority),
        "policyId": authority.policy_id if authority.HasField("policy_id") else None,
    }

    # STALE IS NOT A YES EITHER, and this is the asymmetry that used to be here:
    # silence BEFORE the first reading refused, but silence AFTER one was an
    # implicit yes forever, because the last message was cached and never aged
    # out. An aggregator that died while the vessel read FULL_AUTONOMOUS would
    # keep confirming handovers against that frozen value indefinitely — a
    # safety gate failing open. Checked before the level, because a stale
    # reading is not evidence of any level.
    if max_age_s is not None and age_s is not None and age_s > max_age_s:
        verdict["gate"] = GATE_STALE_AUTHORITY
        verdict["ageSeconds"] = round(float(age_s), 3)
        verdict["reason"] = (
            f"The last operational_authority for this entity is {age_s:.0f}s old, "
            f"past the {max_age_s:.0f}s limit — the vessel has stopped saying whether "
            f"it accepts remote operation, which is not a yes."
        )
        return False, verdict

    if level in NON_AUTHORIZING:
        verdict["gate"] = GATE_NON_AUTHORIZING
        verdict["reason"] = (
            f"Vessel authority is {level_name(level)} — it is not accepting remote "
            f"operation at all."
        )
        return False, verdict

    if level < min_level:
        verdict["gate"] = GATE_BELOW_FLOOR
        verdict["reason"] = (
            f"Vessel authority is {level_name(level)}, below the {level_name(min_level)} "
            f"floor this connector is configured with."
        )
        return False, verdict

    # `reason` on the message is explicitly operators-and-logs only ("consumers
    # MUST NOT parse this"), so it is carried for a human to read, never matched on.
    verdict["gate"] = GATE_CONFIRMED
    verdict["reason"] = f"Vessel authority is {level_name(level)}."
    return True, verdict
