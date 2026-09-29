"""Tests for the emergency path (protocols/emergency_request.yaml).

Shore requests; the vessel decides and counts. These pin what a `.proto` can
state of that: that a request carries no fence of its own and only a RESET
names the one it expects to clear, that the safety fence is the shared Fence
type, that the state has no ARMED and does have RECOVERY_REQUIRED, that every
cause says whether a RESET may clear it, and that the ledger has no DUPLICATE
outcome because a repeated request is answered from the ledger, never
re-executed. Design: crowsnest-dev docs/COMMAND-ARCHITECTURE.md §3.
"""

import keelson
import pytest
from google.protobuf.descriptor import FieldDescriptor as F
from keelson import qos
from keelson.payloads.Fence_pb2 import Fence
from keelson.payloads.SafetyState_pb2 import (
    EmergencyCommand,
    EmergencyRequest,
    SafetyCause,
    SafetyRequestOutcome,
    SafetyRequestResult,
    SafetyState,
)

SUBJECTS = {
    "emergency_request": "keelson.EmergencyRequest",
    "safety_state": "keelson.SafetyState",
    "safety_processed_request": "keelson.SafetyRequestResult",
}


def _fields(cls):
    return cls.DESCRIPTOR.fields_by_name


@pytest.mark.unit
@pytest.mark.parametrize("subject, type_name", sorted(SUBJECTS.items()))
def test_the_subjects_resolve(subject, type_name):
    assert keelson.is_subject_well_known(subject)
    assert keelson.get_subject_schema(subject) == type_name


@pytest.mark.unit
def test_the_request_outranks_a_joystick_sample_and_the_rest_travels_elevated():
    assert qos.profile_name_for("emergency_request") == "realtime"
    assert qos.profile_name_for("safety_state") == "elevated"
    assert qos.profile_name_for("safety_processed_request") == "elevated"


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls, name",
    [
        (EmergencyRequest, "vessel_id"),
        (EmergencyRequest, "request_id"),
        (SafetyState, "vessel_id"),
        (SafetyRequestResult, "vessel_id"),
        (SafetyRequestResult, "request_id"),
    ],
)
def test_key_variables_are_single_string_tokens(cls, name):
    """`{vessel_id}/{request_id}` is matched by one wildcard per chunk. A
    composite id publishes without error and never persists (§7.3)."""
    fd = _fields(cls)[name]
    assert fd.type == F.TYPE_STRING and not fd.is_repeated


# ---------------------------------------------------------------------------
# No fence is ever written from shore.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_request_names_at_most_the_fence_it_expects_to_clear():
    fields = _fields(EmergencyRequest)
    assert "safety_generation" not in fields
    assert "fence" not in fields
    assert fields["expected_stopped_fence"].message_type.full_name == "keelson.Fence"
    assert fields["expected_stopped_fence"].has_presence
    # A STOP carries none; absence is observable.
    stop = EmergencyRequest(command=EmergencyCommand.EMERGENCY_COMMAND_STOP)
    assert not EmergencyRequest.FromString(stop.SerializeToString()).HasField(
        "expected_stopped_fence"
    )


@pytest.mark.unit
def test_a_request_carries_no_lease_token():
    """Caller-free: the writer's right is SAFETY_TRIP under the supervisor's
    own policy, never a token on the steering lease."""
    fields = _fields(EmergencyRequest)
    assert not any("token" in name for name in fields), list(fields)
    assert {"controller_id", "controller_site", "principal"} <= set(fields)


@pytest.mark.unit
def test_a_reset_names_the_masters_assignment():
    fields = _fields(EmergencyRequest)
    assert fields["overall_assignment_id"].type == F.TYPE_STRING
    assert fields["overall_assignment_revision"].type == F.TYPE_UINT64


@pytest.mark.unit
def test_the_commands_are_stop_and_reset():
    assert EmergencyCommand.keys() == [
        "EMERGENCY_COMMAND_UNSPECIFIED",
        "EMERGENCY_COMMAND_STOP",
        "EMERGENCY_COMMAND_RESET",
    ]


# ---------------------------------------------------------------------------
# The state: one truth, derived from its causes.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_safety_fence_is_the_shared_fence_type():
    assert _fields(SafetyState)["safety_generation"].message_type.full_name == (
        "keelson.Fence"
    )
    assert _fields(SafetyRequestResult)["processed_fence"].message_type.full_name == (
        "keelson.Fence"
    )
    assert Fence.DESCRIPTOR.full_name == "keelson.Fence"


@pytest.mark.unit
def test_the_states_have_no_armed_and_do_have_recovery_required():
    keys = SafetyState.State.keys()
    assert keys == [
        "SAFETY_STATE_UNSPECIFIED",
        "SAFETY_STATE_CLEAR",
        "SAFETY_STATE_STOPPED",
        "SAFETY_STATE_LOCKED_OUT",
        "SAFETY_STATE_SAFE_STATE",
        "SAFETY_STATE_RECOVERY_REQUIRED",
    ]
    # The vehicle lifecycle DISARMED / STANDBY / ARMED is a separate state.
    assert not any("ARM" in k for k in keys)
    # The default is never published, and a reader treats it as not CLEAR.
    assert SafetyState.SAFETY_STATE_UNSPECIFIED == 0
    assert SafetyState.SAFETY_STATE_CLEAR != 0


@pytest.mark.unit
def test_every_cause_says_whether_a_reset_may_clear_it():
    fields = _fields(SafetyCause)
    assert fields["clearable_remotely"].type == F.TYPE_BOOL
    assert fields["request_id"].has_presence  # only a SAFETY_TRIP has one
    assert SafetyCause.Type.keys() == [
        "CAUSE_TYPE_UNSPECIFIED",
        "CAUSE_TYPE_SAFETY_TRIP",
        "CAUSE_TYPE_PHYSICAL_STOP",
        "CAUSE_TYPE_EXTERNAL_LEGACY_ESTOP",
        "CAUSE_TYPE_ENVELOPE",
        "CAUSE_TYPE_LOCKOUT",
        "CAUSE_TYPE_SAFETY_STATE_LOST",
        "CAUSE_TYPE_SAFE_STATE",
    ]


@pytest.mark.unit
def test_the_state_reports_the_lockout_and_whether_it_is_simulated():
    fields = _fields(SafetyState)
    assert fields["local_lockout"].type == F.TYPE_BOOL
    assert fields["lockout_simulated"].type == F.TYPE_BOOL
    assert fields["active_causes"].is_repeated
    assert fields["stopped_at"].has_presence  # absent while CLEAR
    assert {"who", "why", "supervisor_id"} <= set(fields)


@pytest.mark.unit
def test_a_stopped_state_round_trips_its_causes():
    state = SafetyState(
        vessel_id="alpha",
        supervisor_id="safety-supervisor/0",
        safety_generation=Fence(term="ab" * 16, generation=7),
        state=SafetyState.SAFETY_STATE_STOPPED,
        active_causes=[
            SafetyCause(
                type=SafetyCause.CAUSE_TYPE_SAFETY_TRIP,
                source="master@ROC-2",
                request_id="req-1",
                clearable_remotely=True,
            ),
            SafetyCause(
                type=SafetyCause.CAUSE_TYPE_PHYSICAL_STOP,
                source="physical-stop",
                clearable_remotely=False,
            ),
        ],
    )
    decoded = SafetyState.FromString(state.SerializeToString())
    assert decoded.safety_generation.generation == 7
    assert [c.clearable_remotely for c in decoded.active_causes] == [True, False]
    assert not decoded.active_causes[1].HasField("request_id")


# ---------------------------------------------------------------------------
# The ledger: one answer per request, never a second execution.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_ledger_has_no_duplicate_outcome():
    """A request_id already in the ledger returns its RECORDED outcome; the
    second sighting of a request is the first answer, not a new result."""
    keys = SafetyRequestOutcome.keys()
    assert not any("DUPLICATE" in k for k in keys), keys
    assert set(keys) >= {
        "SAFETY_REQUEST_OUTCOME_ACCEPTED",
        "SAFETY_REQUEST_OUTCOME_REFUSED_UNAUTHORISED",
        "SAFETY_REQUEST_OUTCOME_REFUSED_STALE_FENCE",
        "SAFETY_REQUEST_OUTCOME_PARTIAL",
        "SAFETY_REQUEST_OUTCOME_REFUSED_ASSIGNMENT_PROJECTION_UNVERIFIED",
        "SAFETY_REQUEST_OUTCOME_REFUSED_RECOVERY_REQUIRED",
        "SAFETY_REQUEST_OUTCOME_REFUSED_NOT_STOPPED",
    }
    assert SafetyRequestOutcome.SAFETY_REQUEST_OUTCOME_UNSPECIFIED == 0


@pytest.mark.unit
def test_the_ledger_entry_echoes_the_command_and_carries_the_processed_fence():
    fields = _fields(SafetyRequestResult)
    assert {"request_id", "command", "outcome", "processed_fence"} <= set(fields)
    assert fields["command"].enum_type.full_name == "keelson.EmergencyCommand"
    assert fields["remaining_causes"].is_repeated
