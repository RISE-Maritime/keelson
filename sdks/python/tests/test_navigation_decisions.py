"""Tests for the navigation decision family.

Four messages model what a vessel's navigation function is doing and deciding:
`navigation_state` (its mode, now), `encounter` (one vessel-to-vessel situation,
restated until it closes), `navigation_advice` (the action it advises) and
`advice_disposition` (what became of that advice, and who decided).

These pin the rules a `.proto` cannot state on its own. The one that matters most
is that advice is a record and never a command: acting on it is a separate,
authorised publish on the vehicle interfaces, so nothing in `NavigationAdvice`
may be readable as an actuator order.
"""

import keelson
import pytest

from keelson import qos
from keelson.payloads.AdviceDisposition_pb2 import AdviceDisposition
from keelson.payloads.Encounter_pb2 import Encounter
from keelson.payloads.NavigationAdvice_pb2 import NavigationAdvice
from keelson.payloads.NavigationState_pb2 import NavigationState
from keelson.payloads.Route_pb2 import ColregSituation

SUBJECTS = {
    "navigation_state": "keelson.NavigationState",
    "encounter": "keelson.Encounter",
    "navigation_advice": "keelson.NavigationAdvice",
    "advice_disposition": "keelson.AdviceDisposition",
}


@pytest.mark.parametrize("subject, type_name", sorted(SUBJECTS.items()))
def test_the_subjects_resolve_and_travel_elevated(subject, type_name):
    """Each reports something true now that an operator or a controller may act on."""
    assert keelson.is_subject_well_known(subject)
    assert keelson.get_subject_schema(subject) == type_name
    assert qos.profile_name_for(subject) == "elevated"


@pytest.mark.parametrize(
    "message_class, name",
    [
        (Encounter, "encounter_id"),
        (Encounter, "voyage_id"),
        (NavigationAdvice, "advice_id"),
        (NavigationAdvice, "voyage_id"),
        (AdviceDisposition, "advice_id"),
        (AdviceDisposition, "voyage_id"),
    ],
)
def test_key_tokens_are_single_strings(message_class, name):
    """These are keyed `{subject}/{voyage_id or novoyage}/{id}`.

    A composite id in either position publishes without error and never persists:
    a `.../encounter/*/*` storage expression does not match it.
    """
    field = message_class.DESCRIPTOR.fields_by_name[name]
    assert field.type == field.TYPE_STRING
    assert not field.is_repeated


def test_advice_carries_proposals_not_actuator_orders():
    """Advice is a record. Nothing in it names an actuator, an axis or a mode.

    Steering and propulsion are proposals whose frame is the `oneof` field, the
    same shape as SteeringOrder / PropulsionOrder, so a proposal cannot name one
    frame and carry a value meant for another.
    """
    names = {f.name for f in NavigationAdvice.DESCRIPTOR.fields}
    forbidden = ("rudder", "throttle", "joystick", "axis", "pct", "rpm", "mode", "arm")
    assert not [n for n in names if any(word in n for word in forbidden)]

    steering = NavigationAdvice.SteeringProposal.DESCRIPTOR
    propulsion = NavigationAdvice.PropulsionProposal.DESCRIPTOR
    assert [o.name for o in steering.oneofs] == ["frame"]
    assert {f.name for f in steering.oneofs[0].fields} == {
        "course_over_ground_deg",
        "heading_deg",
    }
    assert [o.name for o in propulsion.oneofs] == ["frame"]
    assert {f.name for f in propulsion.oneofs[0].fields} == {"sog_mps", "stw_mps"}


def test_a_proposal_holds_one_frame_at_a_time():
    advice = NavigationAdvice()
    advice.steering.course_over_ground_deg = 40.0
    advice.steering.heading_deg = 45.0
    assert advice.steering.WhichOneof("frame") == "heading_deg"
    assert not advice.steering.HasField("course_over_ground_deg")


def test_encounter_situation_extends_colreg_situation_without_renumbering():
    """The live enum repeats the plan enum's values and adds two after them.

    If the two drift, a consumer mapping a planned leg's situation onto a live
    encounter by number reads the wrong rule.
    """
    live = Encounter.Situation.DESCRIPTOR.values_by_name
    plan = ColregSituation.DESCRIPTOR.values_by_name
    for name, value in plan.items():
        assert live[name].number == value.number, name
    assert set(live) - set(plan) == {
        "COLREG_SITUATION_OVERTAKEN",
        "COLREG_SITUATION_NONE",
    }


def test_encounter_keeps_role_and_risk_distinct_from_unknown():
    """UNSPECIFIED is the proto3 default, so it must not mean give-way or no risk."""
    assert Encounter.Role.Name(0) == "ROLE_UNSPECIFIED"
    assert Encounter.Risk.Name(0) == "RISK_NONE"
    assert Encounter.DESCRIPTOR.fields_by_name["cpa_m"].has_presence
    assert Encounter.DESCRIPTOR.fields_by_name["closed_at"].has_presence


def test_disposition_states():
    states = set(AdviceDisposition.State.DESCRIPTOR.values_by_name)
    assert states == {
        "STATE_UNSPECIFIED",
        "STATE_ACCEPTED",
        "STATE_REJECTED",
        "STATE_SUPERSEDED",
        "STATE_EXPIRED",
    }


def test_navigation_state_authority_is_the_operational_authority_enum():
    """One vocabulary for authority levels, never a second copy that can disagree."""
    field = NavigationState.DESCRIPTOR.fields_by_name["authority_level_in_force"]
    assert field.enum_type.full_name == "keelson.OperationalAuthority.AuthorityLevel"
    assert NavigationState.DESCRIPTOR.fields_by_name["route_ref"].has_presence
