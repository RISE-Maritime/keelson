"""authority_manager/v1 and control_gate/v1: the onboard authority manager's control plane, and the
motion gate's side of the two-phase transfer (protocols/command_authority.yaml).

The manager and the gate live in different repos, and the stations that call the manager in more; the
procedure names and the request/reply types are what all of them put on the wire."""

import pytest

from keelson.interfaces_registry import get_procedure_schemas, get_procedures


@pytest.mark.unit
def test_the_gate_serves_prepare_commit_the_reconcile_read_and_the_direct_abort():
    assert get_procedures("control_gate", "v1") == [
        "prepare",
        "commit",
        "get_gate_state",
        "abort_automation",
    ]
    req, resp = get_procedure_schemas("control_gate", "v1", "prepare")
    # The proposal and its answer are the shared payload types, not copies.
    assert (req.full_name, resp.full_name) == (
        "keelson.TransferPrepare",
        "keelson.TransferAck",
    )
    req, _ = get_procedure_schemas("control_gate", "v1", "commit")
    assert req.full_name == "keelson.TransferCommit"


@pytest.mark.unit
def test_no_request_to_the_gate_carries_a_fence_but_the_commit_that_echoes_the_gates_own():
    """I2: only the enforcer mints a fence. The manager's proposal names none; the only fence it
    sends back is the one the gate put in its ACK."""
    for proc in ("prepare", "get_gate_state", "abort_automation"):
        req, _ = get_procedure_schemas("control_gate", "v1", proc)
        fields = {
            f.message_type.full_name for f in req.fields if f.message_type is not None
        }
        assert "keelson.Fence" not in fields, proc
        if proc == "prepare":
            rows = req.fields_by_name["mappings"].message_type
            assert (
                rows.full_name == "keelson.ControlMapping"
            )  # its fence stays unset on a proposal
    req, _ = get_procedure_schemas("control_gate", "v1", "commit")
    assert req.fields_by_name["fence"].message_type.full_name == "keelson.Fence"


@pytest.mark.unit
def test_the_prepare_carries_a_duration_never_a_deadline():
    req, _ = get_procedure_schemas("control_gate", "v1", "prepare")
    assert "prepare_ttl_ms" in req.fields_by_name
    assert not any(
        f.message_type is not None
        and f.message_type.full_name == "google.protobuf.Timestamp"
        for f in req.fields
    )


@pytest.mark.unit
def test_the_direct_abort_names_the_assignment_it_acts_under():
    req, resp = get_procedure_schemas("control_gate", "v1", "abort_automation")
    assert {"assignment_id", "assignment_revision", "session_token"} <= set(
        req.fields_by_name
    )
    assert "mappings" not in req.fields_by_name  # it proposes no map: never a transfer


@pytest.mark.unit
def test_the_manager_serves_the_control_plane():
    assert get_procedures("authority_manager", "v1") == [
        "request",
        "renew",
        "release",
        "transfer",
        "take_control",
        "abort_automation",
        "assume_command",
        "set_navigation_authority",
        "get",
        "history",
        "request_maintenance",
        "cancel_maintenance",
        "finish_maintenance",
        "complete_maintenance_check",
    ]


@pytest.mark.unit
def test_no_request_to_the_manager_carries_a_fence_and_privileged_ones_carry_the_revision():
    for proc in get_procedures("authority_manager", "v1"):
        req, _ = get_procedure_schemas("authority_manager", "v1", proc)
        assert all(
            f.message_type is None or f.message_type.full_name != "keelson.Fence"
            for f in req.fields
        ), proc
    req, _ = get_procedure_schemas("authority_manager", "v1", "take_control")
    caller = req.fields_by_name["caller"].message_type
    assert {"assignment_id", "assignment_revision", "token"} <= set(
        caller.fields_by_name
    )


@pytest.mark.unit
def test_there_is_no_force_safety_release_anywhere():
    for iface in ("authority_manager", "control_gate"):
        assert not any(
            "safety" in p or "release_stop" in p for p in get_procedures(iface, "v1")
        )


@pytest.mark.unit
def test_refusals_are_a_closed_typed_set():
    from keelson.interfaces.AuthorityManager_pb2 import Refusal

    names = set(Refusal.keys())
    assert {
        "REFUSAL_HELD",
        "REFUSAL_STALE_REVISION",
        "REFUSAL_NOT_RECONCILED",
        "REFUSAL_GATE",
        "REFUSAL_MAP",
    } <= names
    assert Refusal.Value("REFUSAL_UNSPECIFIED") == 0


@pytest.mark.unit
def test_messages_round_trip():
    from keelson.interfaces.AuthorityManager_pb2 import (
        AuthorityReply,
        Caller,
        TakeControlRequest,
    )
    from keelson.interfaces.ControlGate_pb2 import CommitResponse, GateState
    from keelson.payloads.ControlMapping_pb2 import ControlMapping

    take = TakeControlRequest(
        caller=Caller(
            controller_id="anna@ROC-2",
            controller_site="ROC-2",
            token="s",
            assignment_id="as-1",
            assignment_revision=4,
        ),
        mappings=[ControlMapping(dof="rudder", execution_source="ROC-2/helm-1/rudder")],
    )
    assert TakeControlRequest.FromString(take.SerializeToString()) == take
    state = GateState(
        vessel_id="usv-alpha",
        fence={"term": "ab" * 16, "generation": 42},
        in_transition=True,
    )
    back = GateState.FromString(state.SerializeToString())
    assert (
        back.fence.generation == 42
        and back.in_transition
        and not back.HasField("reserved_transfer_id")
    )
    assert not CommitResponse().applied
    assert not AuthorityReply().HasField("fence")
