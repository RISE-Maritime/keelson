"""container_control/v1 actions only in MAINTENANCE (protocols/maintenance.yaml, crowsnest-dev#303).

With --maintenance-vessel set, start / stop / restart / remove need that vessel's maintenance_state to be
a fresh MAINTENANCE: any other state, a stale record and an unheard one are refused INVALID_STATE with a
reason naming why, before the backend is touched. list and logs stay open. Unset, nothing changes.
"""

import logging
from dataclasses import replace

import pytest
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.ContainerControl_pb2 import (
    ListContainersRequest,
    RemoveContainerRequest,
    StopContainerRequest,
)
from keelson.maintenance import MaintenanceWatch
from keelson.payloads.MaintenanceState_pb2 import MaintenanceState

from container_control import handlers

from container_control_fakes import FakeOp

V = "usv-alpha"
S = MaintenanceState.State


class Clock:
    def __init__(self):
        self.t = 1_000

    def __call__(self):
        return self.t


def run(ctx, procedure, request=None) -> FakeOp:
    op = FakeOp(
        procedure=procedure,
        request_bytes=request.SerializeToString() if request else b"",
    )
    handlers.build(ctx)[0][procedure](op)
    return op


def gated(ctx, clock, state=None):
    watch = MaintenanceWatch(V, max_age_ms=15_000, now_ms=clock)
    if state is not None:
        watch.observe(
            MaintenanceState(
                vessel_id=V, state=state, maintenance_id="m-7", revision=12
            )
        )
    return replace(ctx, maintenance=watch)


@pytest.fixture
def clock():
    return Clock()


@pytest.mark.parametrize("verb", ["start", "stop", "restart"])
def test_in_maintenance_an_action_runs_and_is_recorded(
    control_ctx, clock, verb, caplog
):
    ctx = gated(control_ctx, clock, S.STATE_MAINTENANCE)
    with caplog.at_level(logging.WARNING, logger="container_control"):
        op = run(ctx, verb, StopContainerRequest(name="keelson-router"))
    assert op.err is None and op.replies == 1
    assert ctx.backend.calls[0][:2] == (verb, "keelson-router")
    assert "under maintenance m-7 (revision 12)" in caplog.text


@pytest.mark.parametrize(
    "state",
    [
        S.STATE_UNSPECIFIED,
        S.STATE_INACTIVE,
        S.STATE_REQUESTED,
        S.STATE_PREPARING,
        S.STATE_READY_FOR_CHECK,
    ],
)
@pytest.mark.parametrize("verb", ["start", "stop", "restart"])
def test_in_any_other_state_it_is_refused_naming_the_state(
    control_ctx, clock, verb, state
):
    ctx = gated(control_ctx, clock, state)
    op = run(ctx, verb, StopContainerRequest(name="keelson-router"))
    name = S.Name(state).removeprefix("STATE_")
    assert op.err[1] == ErrorResponse.Code.INVALID_STATE and op.replies == 1
    assert f"needs maintenance_state MAINTENANCE: {V} is {name}" in op.err[0]
    assert ctx.backend.calls == []


def test_unheard_is_refused(control_ctx, clock):
    op = run(
        gated(control_ctx, clock),
        "restart",
        StopContainerRequest(name="keelson-router"),
    )
    assert (
        op.err[1] == ErrorResponse.Code.INVALID_STATE
        and "no maintenance_state heard" in op.err[0]
    )


def test_stale_is_refused_and_a_fresh_heartbeat_restores_it(control_ctx, clock):
    ctx = gated(control_ctx, clock, S.STATE_MAINTENANCE)
    clock.t += 15_000
    assert (
        run(ctx, "restart", StopContainerRequest(name="keelson-router")).err is None
    )  # exactly the window
    clock.t += 1
    op = run(ctx, "restart", StopContainerRequest(name="keelson-router"))
    assert op.err[1] == ErrorResponse.Code.INVALID_STATE and "15001 ms old" in op.err[0]
    ctx.maintenance.observe(MaintenanceState(vessel_id=V, state=S.STATE_MAINTENANCE))
    assert run(ctx, "restart", StopContainerRequest(name="keelson-router")).err is None


def test_remove_is_gated_too(remove_ctx, clock):
    ctx = gated(remove_ctx, clock, S.STATE_INACTIVE)
    op = run(ctx, "remove", RemoveContainerRequest(name="grafana"))
    assert op.err[1] == ErrorResponse.Code.INVALID_STATE and ctx.backend.calls == []
    ctx.maintenance.observe(MaintenanceState(vessel_id=V, state=S.STATE_MAINTENANCE))
    assert run(ctx, "remove", RemoveContainerRequest(name="grafana")).err is None


def test_the_guard_still_speaks_first(readonly_ctx, clock):
    """Control off says so, whatever the procedure: the operator's next step is the responder's flag."""
    op = run(
        gated(readonly_ctx, clock), "stop", StopContainerRequest(name="keelson-router")
    )
    assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED


def test_reads_stay_open(control_ctx, clock):
    op = run(gated(control_ctx, clock), "list", ListContainersRequest())
    assert op.err is None and op.replies == 1


def test_another_vessels_record_is_not_this_vessels_procedure(control_ctx, clock):
    ctx = gated(control_ctx, clock)
    ctx.maintenance.observe(
        MaintenanceState(vessel_id="usv-beta", state=S.STATE_MAINTENANCE)
    )
    assert (
        run(ctx, "restart", StopContainerRequest(name="keelson-router")).err[1]
        == ErrorResponse.Code.INVALID_STATE
    )


def test_not_opted_in_nothing_changes(control_ctx):
    assert control_ctx.maintenance is None
    assert (
        run(control_ctx, "restart", StopContainerRequest(name="keelson-router")).err
        is None
    )
