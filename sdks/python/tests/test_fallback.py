"""Fallback — safety_policy/v1 and the fallback_policy / fallback_state subjects.

See protocols/fallback.yaml and docs/decisions/0001-coxswain-conn.md R5.
"""

from __future__ import annotations

import keelson
from keelson import interfaces


def test_safety_policy_v1_is_registered():
    assert keelson.is_interface_well_known("safety_policy/v1")
    assert (
        keelson.get_interface_service("safety_policy/v1")
        == "keelson.interfaces.safety_policy.SafetyPolicy"
    )
    assert sorted(interfaces.get_procedures("safety_policy", "v1")) == [
        "clear",
        "get",
        "set",
    ]


def test_every_procedure_replies_a_policy_reply():
    expected_requests = {
        "get": "GetPolicyRequest",
        "set": "SetPolicyRequest",
        "clear": "ClearRequest",
    }
    for procedure, request in expected_requests.items():
        request_cls, reply_cls = interfaces.get_procedure_message_classes(
            "safety_policy", "v1", procedure
        )
        assert (
            request_cls.DESCRIPTOR.full_name
            == f"keelson.interfaces.safety_policy.{request}"
        )
        assert (
            reply_cls.DESCRIPTOR.full_name
            == "keelson.interfaces.safety_policy.PolicyReply"
        )


def test_actions_are_ordered_by_severity():
    # The most severe tripped rule is in force, so the numbering IS the order
    # (protocols/fallback.yaml invariants). Renumbering would silently change
    # which recovery wins.
    from keelson.payloads.Fallback_pb2 import FallbackAction

    assert [FallbackAction.Name(i) for i in range(6)] == [
        "FALLBACK_ACTION_UNSPECIFIED",
        "FALLBACK_ACTION_CONTINUE",
        "FALLBACK_ACTION_HOLD",
        "FALLBACK_ACTION_RETURN_HOME",
        "FALLBACK_ACTION_STANDBY",
        "FALLBACK_ACTION_STOP",
    ]


def _the_example_policy():
    from keelson.payloads.Fallback_pb2 import FallbackAction, FallbackPolicy

    policy = FallbackPolicy(revision=3)
    link = policy.rules.add(
        rule_id="link_2min", hold_s=120, action=FallbackAction.FALLBACK_ACTION_HOLD
    )
    link.control_station_link_lost.SetInParent()
    home = policy.rules.add(
        rule_id="link_10min",
        hold_s=600,
        action=FallbackAction.FALLBACK_ACTION_RETURN_HOME,
    )
    home.control_station_link_lost.SetInParent()
    gnss = policy.rules.add(
        rule_id="gnss", hold_s=5, action=FallbackAction.FALLBACK_ACTION_STANDBY
    )
    gnss.position_insufficient.max_age_s = 3
    gnss.position_insufficient.max_horizontal_error_m = 10
    roll = policy.rules.add(
        rule_id="roll", hold_s=2, action=FallbackAction.FALLBACK_ACTION_STOP
    )
    roll.envelope_limit.subject = "roll_deg"
    roll.envelope_limit.min = -25
    roll.envelope_limit.max = 25
    roll.envelope_limit.max_age_s = 1
    return policy


def test_a_rule_carries_exactly_one_condition():
    policy = _the_example_policy()
    assert [r.WhichOneof("condition") for r in policy.rules] == [
        "control_station_link_lost",
        "control_station_link_lost",
        "position_insufficient",
        "envelope_limit",
    ]


def test_an_envelope_limit_keeps_an_absent_bound_absent():
    from keelson.payloads.Fallback_pb2 import FallbackPolicy

    rule = FallbackPolicy.Rule(rule_id="speed")
    rule.envelope_limit.subject = "speed_through_water_knots"
    rule.envelope_limit.max = 12
    # A missing lower bound must not decode as 0: a reading of -0.1 would trip.
    decoded = FallbackPolicy.Rule.FromString(rule.SerializeToString())
    assert not decoded.envelope_limit.HasField("min")
    assert decoded.envelope_limit.HasField("max")


def test_fallback_subjects_resolve_and_round_trip_through_an_envelope():
    from keelson.payloads.Fallback_pb2 import FallbackAction, FallbackState

    assert keelson.get_subject_schema("fallback_policy") == "keelson.FallbackPolicy"
    assert keelson.get_subject_schema("fallback_state") == "keelson.FallbackState"

    state = FallbackState(
        policy_revision=3,
        action=FallbackAction.FALLBACK_ACTION_HOLD,
        requested_action=FallbackAction.FALLBACK_ACTION_RETURN_HOME,
        rule_id="link_10min",
        latched=True,
        transition_count=2,
        home_defined=False,
    )
    status = state.rules.add(
        rule_id="gnss", state=FallbackState.RuleStatus.State.STATE_NO_SOURCE
    )
    assert not status.HasField("value")

    samples = {"fallback_policy": _the_example_policy(), "fallback_state": state}
    for subject, message in samples.items():
        _, _, payload = keelson.uncover(keelson.enclose(message.SerializeToString()))
        decoded = keelson.decode_protobuf_payload_from_type_name(
            payload, keelson.get_subject_schema(subject)
        )
        assert decoded.SerializeToString() == message.SerializeToString(), subject


def test_policy_reply_never_reports_ok_as_the_default():
    # A reply whose result was never set must not read as success.
    from keelson.interfaces.SafetyPolicy_pb2 import PolicyReply

    assert PolicyReply().result == PolicyReply.Result.RESULT_UNSPECIFIED
    assert PolicyReply.Result.RESULT_OK != 0
