"""keelson.maintenance: a gatekeeper's view of maintenance_state (protocols/maintenance.yaml).

Judged on the gatekeeper's own clock from local receipt; a stale or unheard record satisfies nothing; a
refusal names what was required and what was heard.
"""

import pytest
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.maintenance import (
    INACTIVE,
    MAINTENANCE,
    MaintenanceRefused,
    MaintenanceWatch,
    state_name,
)
from keelson.payloads.MaintenanceState_pb2 import MaintenanceState

pytestmark = pytest.mark.unit

V = "usv-alpha"
S = MaintenanceState.State


class Clock:
    def __init__(self):
        self.t = 5_000

    def __call__(self):
        return self.t


def watch(clock, state=None, max_age_ms=15_000, **kw):
    w = MaintenanceWatch(V, max_age_ms=max_age_ms, now_ms=clock)
    if state is not None:
        w.observe(MaintenanceState(vessel_id=V, state=state, **kw))
    return w


def test_the_required_state_fresh_is_allowed_and_names_the_procedure():
    clock = Clock()
    w = watch(clock, S.STATE_MAINTENANCE, maintenance_id="m-9", revision=4)
    assert w.refusal(MAINTENANCE, "set_config") is None
    heard = w.require(MAINTENANCE, "set_config")
    assert (heard.maintenance_id, heard.revision, heard.heard_ms) == ("m-9", 4, 5_000)


@pytest.mark.parametrize("state", [s for s in S.values() if s != S.STATE_INACTIVE])
def test_every_other_state_refuses_naming_both(state):
    why = watch(Clock(), state).refusal(INACTIVE, "arm")
    assert (
        why
        == f"arm needs maintenance_state INACTIVE: {V} is {S.Name(state).removeprefix('STATE_')}"
    )


def test_unheard_refuses():
    assert "no maintenance_state heard for usv-alpha" in watch(Clock()).refusal(
        MAINTENANCE, "restart"
    )


def test_stale_from_local_receipt_at_the_boundary():
    clock = Clock()
    w = watch(clock, S.STATE_INACTIVE, max_age_ms=1000)
    clock.t += 1000
    assert w.refusal(INACTIVE, "arm") is None
    clock.t += 1
    assert "1001 ms old (stale after 1000 ms)" in w.refusal(INACTIVE, "arm")


def test_another_vessels_record_is_ignored():
    clock = Clock()
    w = watch(clock, S.STATE_MAINTENANCE)
    w.observe(MaintenanceState(vessel_id="usv-beta", state=S.STATE_INACTIVE))
    assert w.refusal(MAINTENANCE, "x") is None


def test_the_refusal_is_typed_invalid_state():
    with pytest.raises(MaintenanceRefused, match="is INACTIVE") as e:
        watch(Clock(), S.STATE_INACTIVE).require(MAINTENANCE, "set_config")
    assert e.value.error_code == ErrorResponse.Code.INVALID_STATE


def test_an_unknown_state_number_is_named_as_a_number():
    assert (
        state_name(42) == "state 42"
        and state_name(S.STATE_READY_FOR_CHECK) == "READY_FOR_CHECK"
    )
