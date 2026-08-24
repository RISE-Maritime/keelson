"""Whether the vessel confirms a watch handover, and why.

The rule under test is short; what makes it worth testing is that every way of
getting it wrong confirms a handover the vessel is actually refusing.
"""

import pytest

from watch_handover.verdict import (
    DEFAULT_MIN_LEVEL,
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
