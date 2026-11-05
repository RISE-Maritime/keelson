"""NavigationState reports the functions the navigation process holds at control method 3 or above,
and the navigation authority revision it read them under (crowsnest-dev#90)."""

from keelson.payloads.NavigationAuthority_pb2 import (
    CommsLossBehaviour,
    ControlMethod,
    FunctionAuthority,
    NavigationFunction,
)
from keelson.payloads.NavigationState_pb2 import NavigationState


def test_functions_in_force_round_trip():
    s = NavigationState(navigation_authority_revision=7)
    f = s.functions_in_force.add(
        function=NavigationFunction.NAVIGATION_FUNCTION_COLLISION_AVOIDANCE,
        holder="navigation/0@usv-alpha",
        control_method=ControlMethod.CONTROL_METHOD_DELEGATED,
    )
    f.envelope.intervention_window_ms = 30000
    f.envelope.intent_delivery_required = True
    f.envelope.comms_loss_behaviour = CommsLossBehaviour.COMMS_LOSS_BEHAVIOUR_FALLBACK_TO_METHOD_2
    back = NavigationState.FromString(s.SerializeToString())
    assert back.navigation_authority_revision == 7
    (only,) = back.functions_in_force
    assert only.function == NavigationFunction.NAVIGATION_FUNCTION_COLLISION_AVOIDANCE
    assert only.control_method == ControlMethod.CONTROL_METHOD_DELEGATED
    assert only.envelope.intervention_window_ms == 30000


def test_the_new_fields_take_the_next_numbers_and_the_document_type():
    fields = NavigationState.DESCRIPTOR.fields_by_name
    assert fields["functions_in_force"].number == 12
    assert fields["functions_in_force"].message_type.full_name == FunctionAuthority.DESCRIPTOR.full_name
    assert fields["navigation_authority_revision"].number == 13


def test_an_empty_report_is_none_held_and_revision_zero():
    s = NavigationState.FromString(NavigationState().SerializeToString())
    assert list(s.functions_in_force) == [] and s.navigation_authority_revision == 0
