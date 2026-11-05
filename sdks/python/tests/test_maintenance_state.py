"""maintenance_state and authority_manager/v1's maintenance calls (protocols/maintenance.yaml).

The authority manager writes the record and the stations, the supervisor and the gate read it, in
different repos; the subject, the states and the call shapes are what all of them put on the wire."""

import pytest

import keelson
from keelson import qos
from keelson.interfaces_registry import get_procedure_schemas, get_procedures
from keelson.payloads.MaintenanceState_pb2 import MaintenanceState

pytestmark = pytest.mark.unit

STATES = [
    "STATE_UNSPECIFIED",
    "STATE_INACTIVE",
    "STATE_REQUESTED",
    "STATE_PREPARING",
    "STATE_MAINTENANCE",
    "STATE_READY_FOR_CHECK",
]
SCOPES = [
    "SCOPE_UNSPECIFIED",
    "SCOPE_SOFTWARE",
    "SCOPE_FIRMWARE",
    "SCOPE_NETWORK",
    "SCOPE_CALIBRATION",
    "SCOPE_DEPLOYMENT",
    "SCOPE_CONFIGURATION",
]


def test_the_subject_is_the_maintenance_state_slot_on_the_elevated_profile():
    assert keelson.get_subject_schema("maintenance_state") == "keelson.MaintenanceState"
    assert qos.profile_name_for("maintenance_state") == "elevated"


def test_the_states_and_scopes_in_order_and_zero_is_never_a_state():
    assert [v.name for v in MaintenanceState.State.DESCRIPTOR.values] == STATES
    assert [v.number for v in MaintenanceState.State.DESCRIPTOR.values] == list(range(6))
    assert [v.name for v in MaintenanceState.Scope.DESCRIPTOR.values] == SCOPES
    # A record that never set its state does not read as INACTIVE.
    assert MaintenanceState().state != MaintenanceState.STATE_INACTIVE


def test_a_record_round_trips_with_who_why_scope_guards_and_checks():
    rec = MaintenanceState(
        vessel_id="sf18",
        state=MaintenanceState.STATE_REQUESTED,
        maintenance_id="m-1",
        revision=7,
        requested_by="ted@ROC-1",
        overall_assignment_id="a-1",
        overall_assignment_revision=3,
        maintainer="anna@workshop",
        reason="autopilot firmware",
        scopes=[MaintenanceState.SCOPE_FIRMWARE, MaintenanceState.SCOPE_CONFIGURATION],
        guards=[MaintenanceState.Guard(name="disarmed", satisfied=False, detail="vehicle_armed is true")],
        checks=[MaintenanceState.CheckResult(name="rudder_sweep", passed=True, evidence="run-9")],
        manager_id="authority-manager",
    )
    rec.issued_at.FromSeconds(1_800_000_000)
    rec.since.FromSeconds(1_800_000_010)
    back = MaintenanceState.FromString(rec.SerializeToString())
    assert back == rec
    assert back.HasField("issued_at") and back.HasField("since")
    assert (back.guards[0].name, back.guards[0].satisfied) == ("disarmed", False)


def test_the_manager_serves_four_maintenance_calls_after_the_existing_ones():
    procs = get_procedures("authority_manager", "v1")
    assert procs[-4:] == [
        "request_maintenance",
        "cancel_maintenance",
        "finish_maintenance",
        "complete_maintenance_check",
    ]
    for proc in procs[-4:]:
        req, resp = get_procedure_schemas("authority_manager", "v1", proc)
        assert resp.full_name == "keelson.interfaces.authority_manager.MaintenanceReply"
        assert req.fields_by_name["caller"].message_type.name == "Caller", proc
        assert resp.fields_by_name["record"].message_type.full_name == "keelson.MaintenanceState"


def test_a_request_names_who_does_it_why_and_what_it_may_touch():
    req, _ = get_procedure_schemas("authority_manager", "v1", "request_maintenance")
    assert {"maintainer", "reason", "scopes"} <= set(req.fields_by_name)
    assert req.fields_by_name["scopes"].enum_type.full_name == "keelson.MaintenanceState.Scope"
    req, _ = get_procedure_schemas("authority_manager", "v1", "complete_maintenance_check")
    assert req.fields_by_name["checks"].message_type.full_name == "keelson.MaintenanceState.CheckResult"


def test_no_maintenance_call_arms_or_commands_the_vehicle():
    """Leaving maintenance never arms: no request carries an arm, a mode or a mapping."""
    for proc in get_procedures("authority_manager", "v1")[-4:]:
        req, _ = get_procedure_schemas("authority_manager", "v1", proc)
        names = set(req.fields_by_name)
        assert not names & {"arm", "mode", "mappings"}, proc


def test_the_refusals_and_the_state_view_carry_maintenance():
    _, resp = get_procedure_schemas("authority_manager", "v1", "request")
    refusal = resp.fields_by_name["refusal"].enum_type
    assert refusal.values_by_name["REFUSAL_MAINTENANCE"].number == 11
    assert refusal.values_by_name["REFUSAL_GUARD"].number == 12
    _, state = get_procedure_schemas("authority_manager", "v1", "get")
    field = state.fields_by_name["maintenance"]
    assert (field.number, field.message_type.full_name) == (9, "keelson.MaintenanceState")


def test_maintenance_leaves_only_through_the_checks():
    """The protocol's lifecycle has no edge out of MAINTENANCE or READY_FOR_CHECK to INACTIVE but
    `close`, which needs every required check passed: no cancel once anything may have changed."""
    from pathlib import Path

    import yaml

    spec = Path(__file__).resolve().parents[3] / "protocols" / "maintenance.yaml"
    if not spec.is_file():
        pytest.skip("protocols/ only exists in a repository checkout")
    (lc,) = yaml.safe_load(spec.read_text())["lifecycle"]
    into_inactive = {(t.get("from"), t["action"]) for t in lc["transitions"] if t["to"] == "inactive"}
    assert into_inactive == {
        ("ready_for_check", "close"),
        ("requested", "withdraw"),
        ("preparing", "withdraw"),
    }
    out_of_maintenance = {t["to"] for t in lc["transitions"] if t.get("from") == "maintenance"}
    assert out_of_maintenance == {"ready_for_check"}
