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
    f.envelope.comms_loss_behaviour = (
        CommsLossBehaviour.COMMS_LOSS_BEHAVIOUR_FALLBACK_TO_METHOD_2
    )
    back = NavigationState.FromString(s.SerializeToString())
    assert back.navigation_authority_revision == 7
    (only,) = back.functions_in_force
    assert only.function == NavigationFunction.NAVIGATION_FUNCTION_COLLISION_AVOIDANCE
    assert only.control_method == ControlMethod.CONTROL_METHOD_DELEGATED
    assert only.envelope.intervention_window_ms == 30000


def test_the_new_fields_take_the_next_numbers_and_the_document_type():
    fields = NavigationState.DESCRIPTOR.fields_by_name
    assert fields["functions_in_force"].number == 12
    assert (
        fields["functions_in_force"].message_type.full_name
        == FunctionAuthority.DESCRIPTOR.full_name
    )
    assert fields["navigation_authority_revision"].number == 13


def test_an_empty_report_is_none_held_and_revision_zero():
    s = NavigationState.FromString(NavigationState().SerializeToString())
    assert list(s.functions_in_force) == [] and s.navigation_authority_revision == 0


def test_the_human_decision_in_force_has_an_id_the_process_drew_and_absence_is_absence():
    s = NavigationState(order_id="o-7", ordered_by="anna@ROC-1")
    back = NavigationState.FromString(s.SerializeToString())
    assert back.HasField("order_id") and (back.order_id, back.ordered_by) == (
        "o-7",
        "anna@ROC-1",
    )
    # No human decision in force reads as absent, never as an empty id a gate could match.
    assert not NavigationState.FromString(
        NavigationState().SerializeToString()
    ).HasField("order_id")
    fields = NavigationState.DESCRIPTOR.fields_by_name
    assert (fields["order_id"].number, fields["ordered_by"].number) == (14, 15)
