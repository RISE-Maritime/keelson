"""Whether the vessel confirms a watch handover, and why.

The rule under test is short; what makes it worth testing is that every way of
getting it wrong confirms a handover the vessel is actually refusing.
"""

import pytest

from watch_handover.verdict import (
    DEFAULT_MIN_LEVEL,
    GATE_BELOW_FLOOR,
    GATE_CONFIRMED,
    GATE_NON_AUTHORIZING,
    GATE_NO_AUTHORITY,
    GATE_STALE_AUTHORITY,
    LEVEL_NAMES,
    NON_AUTHORIZING,
    decide,
    level_name,
)


class FakeConstraint:
    def __init__(self, component_id, cap_level, cause, subject_id=None):
        self.component_id = component_id
        self.cap_level = cap_level
        self.cause = cause
        self.subject_id = subject_id or ""
        self._has_subject = subject_id is not None

    def HasField(self, name):
        return self._has_subject if name == "subject_id" else False


class FakeAuthority:
    """Enough of OperationalAuthority to exercise `decide`."""

    def __init__(self, level, constraints=(), policy_id=None, composite_score=None):
        self.level = level
        self.active_constraints = list(constraints)
        self.policy_id = policy_id or ""
        self.composite_score = composite_score
        self._has_policy = policy_id is not None

    def HasField(self, name):
        return self._has_policy if name == "policy_id" else False


def test_silence_is_not_a_yes():
    """Nothing on the wire means the vessel has not said it accepts remote work."""
    confirmed, verdict = decide(None)
    assert confirmed is False
    assert verdict["level"] == 0
    assert "cannot say" in verdict["reason"]


@pytest.mark.parametrize("level", sorted(NON_AUTHORIZING))
def test_non_authorizing_levels_refuse(level):
    """UNKNOWN and MINIMAL_SAFE_MODE are both refusals.

    The proto is explicit that UNKNOWN "MUST be treated as non-authorizing,
    exactly like MINIMAL_SAFE_MODE, and MUST NOT be treated as 'no constraints
    known'" — it means the monitor could not tell, which is not a yes.
    """
    confirmed, verdict = decide(FakeAuthority(level))
    assert confirmed is False
    assert verdict["levelName"] == level_name(level)


def test_confirms_at_the_floor():
    confirmed, verdict = decide(FakeAuthority(2), min_level=2)
    assert confirmed is True
    assert verdict["levelName"] == "SUPERVISED_REMOTE"


def test_confirms_above_the_floor():
    assert decide(FakeAuthority(5), min_level=2)[0] is True


def test_refuses_below_the_floor():
    confirmed, verdict = decide(FakeAuthority(2), min_level=3)
    assert confirmed is False
    assert "below the REMOTE_CONTROLLED floor" in verdict["reason"]


def test_a_healthy_composite_never_buys_back_a_capped_level():
    """The MUST that matters most.

    `composite_score` is uncapped by design: one hard-down essential component
    leaves it at 0.917 while `level` is refusing outright. A connector that read
    the aggregate would confirm a handover the vessel is vetoing.
    """
    authority = FakeAuthority(1, composite_score=0.917)
    confirmed, _ = decide(authority, min_level=DEFAULT_MIN_LEVEL)
    assert confirmed is False


def test_a_refusal_carries_the_constraints_that_caused_it():
    authority = FakeAuthority(
        1,
        constraints=[FakeConstraint("gnss-1", 1, 5, subject_id="location_fix")],
        policy_id="policy-a",
    )
    confirmed, verdict = decide(authority)
    assert confirmed is False
    assert verdict["constraints"][0]["componentId"] == "gnss-1"
    assert verdict["constraints"][0]["cause"] == "NOT_ADVERTISED"
    assert verdict["constraints"][0]["subjectId"] == "location_fix"
    assert verdict["policyId"] == "policy-a"


def test_a_confirmation_is_an_audit_trail_too():
    """Kept whichever way it goes — why the vessel WAS willing is worth recording."""
    _, verdict = decide(FakeAuthority(3, policy_id="policy-a"))
    assert verdict["levelName"] == "REMOTE_CONTROLLED"
    assert verdict["policyId"] == "policy-a"
    assert verdict["reason"]


def test_an_unknown_future_level_still_names_itself():
    """A level this build has never heard of must not render as blank."""
    assert level_name(9) == "LEVEL_9"
    # And it is above the floor, so it confirms — a NEWER level is more
    # authority, not less. Refusing it would make an upgrade look like a fault.
    assert decide(FakeAuthority(9))[0] is True


def test_a_constraint_without_a_subject_is_not_invented():
    authority = FakeAuthority(1, constraints=[FakeConstraint("battery", 1, 2)])
    _, verdict = decide(authority)
    assert verdict["constraints"][0]["subjectId"] is None


# ── the verdict has to be interpretable long after the fact ──────────────
#
# The verdict is stored on the handover key and read back to ask whether the
# floor is set right. `level` alone cannot answer that: you need the bar it was
# judged against, and WHICH gate fired, because the two mean opposite things —
# a non_authorizing refusal says nothing about the floor, a below_floor refusal
# is entirely about it. Neither was recorded before, so no stored refusal could
# be re-judged against a different setting.


@pytest.mark.parametrize(
    "authority,min_level,expected_gate",
    [
        (None, 2, GATE_NO_AUTHORITY),
        (FakeAuthority(0), 2, GATE_NON_AUTHORIZING),
        (FakeAuthority(1), 2, GATE_NON_AUTHORIZING),
        (FakeAuthority(2), 3, GATE_BELOW_FLOOR),
        (FakeAuthority(2), 2, GATE_CONFIRMED),
        (FakeAuthority(5), 2, GATE_CONFIRMED),
    ],
)
def test_every_verdict_names_its_gate(authority, min_level, expected_gate):
    _, verdict = decide(authority, min_level)
    assert verdict["gate"] == expected_gate


@pytest.mark.parametrize(
    "authority", [None, FakeAuthority(0), FakeAuthority(3), FakeAuthority(5)]
)
def test_every_verdict_carries_the_bar_it_was_judged_against(authority):
    # Including the no-authority and non-authorizing branches, which never
    # mentioned the floor at all — not even in the prose.
    _, verdict = decide(authority, min_level=4)
    assert verdict["minLevel"] == 4
    assert verdict["minLevelName"] == "ASSISTED_AUTONOMOUS"


def test_the_floor_is_inert_below_three():
    # NON_AUTHORIZING refuses 0 and 1 whatever the flag says, and 2 clears any
    # floor of 2 or less — so these three settings cannot be told apart by any
    # outcome. A deployment that wants the floor to decide something must set 3+.
    for level in sorted(LEVEL_NAMES):
        outcomes = {decide(FakeAuthority(level), min_level=m)[0] for m in (0, 1, 2)}
        assert len(outcomes) == 1, f"level {level} differs across min_level 0/1/2"
    # And at 3 it starts to bite: level 2 confirms at floor 2, refuses at floor 3.
    assert decide(FakeAuthority(2), min_level=2)[0] is True
    assert decide(FakeAuthority(2), min_level=3)[0] is False


def test_the_default_floor_is_one_that_can_actually_refuse():
    """The default must sit where GATE_BELOW_FLOOR is reachable.

    A default of 2 or less makes the flag inert: it reads as a safety control in
    a deployment's config and cannot refuse anything the protocol was not already
    refusing. Pinned here because that is a silent failure — nothing errors, the
    gate simply never fires.
    """
    assert DEFAULT_MIN_LEVEL >= 3

    # SUPERVISED_REMOTE: authorizing, so only the floor can turn it down.
    confirmed, verdict = decide(FakeAuthority(2), min_level=DEFAULT_MIN_LEVEL)
    assert confirmed is False
    assert verdict["gate"] == GATE_BELOW_FLOOR

    # And the default still confirms a vessel that is fully available.
    assert decide(FakeAuthority(3), min_level=DEFAULT_MIN_LEVEL)[0] is True


# ── stale is not a yes either ────────────────────────────────────────────
#
# The asymmetry this closes: silence BEFORE the first reading refused, but
# silence AFTER one was an implicit yes forever, because the last message was
# cached and never aged out. An aggregator dying while the vessel read
# FULL_AUTONOMOUS kept confirming handovers against that frozen value.


def test_a_stale_reading_refuses_however_high_the_level():
    confirmed, verdict = decide(FakeAuthority(5), age_s=120, max_age_s=30)
    assert confirmed is False
    assert verdict["gate"] == GATE_STALE_AUTHORITY
    assert verdict["ageSeconds"] == 120
    assert "120s old" in verdict["reason"]


def test_a_fresh_reading_is_unaffected():
    assert decide(FakeAuthority(5), age_s=5, max_age_s=30)[0] is True


def test_staleness_is_checked_before_the_level():
    # A stale LOW reading must report staleness, not the level — the level is
    # not known any more, and saying "MINIMAL_SAFE_MODE" would assert something
    # about the vessel that the connector cannot currently see.
    _, verdict = decide(FakeAuthority(1), age_s=99, max_age_s=30)
    assert verdict["gate"] == GATE_STALE_AUTHORITY


def test_no_max_age_keeps_the_old_behaviour():
    # Explicitly opting out must not start refusing, or upgrading the connector
    # would silently begin stranding operators on deployments that never set it.
    assert decide(FakeAuthority(5), age_s=99999, max_age_s=None)[0] is True
    assert decide(FakeAuthority(5), age_s=None, max_age_s=30)[0] is True


# ── the mirror has to actually mirror ────────────────────────────────────


def test_level_names_match_the_generated_enum():
    """The reason LEVEL_NAMES is a hand-written copy.

    verdict.py says it is "mirrored rather than imported ... so a rename upstream
    shows up as a test failure here rather than a silent renumber" — but nothing
    compared the two, so a renumber was silent after all. This is that comparison.
    """
    pb = pytest.importorskip(
        "keelson.payloads.OperationalAuthority_pb2",
        reason="generated protobuf not built; run generate_python.sh",
    )
    enum = pb.OperationalAuthority.AuthorityLevel
    generated = {
        enum.Value(name): name.removeprefix("AUTHORITY_LEVEL_") for name in enum.keys()
    }
    assert generated == LEVEL_NAMES
