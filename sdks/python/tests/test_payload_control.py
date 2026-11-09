"""payload_control/v1, payload_clearance and the platform's payloads[] (protocols/payload_control.yaml).

A payload has an authority and a gate of its own and is never a motion DOF. What ties it to the ship
is clearance as a continuing condition: the conn holder's grant, bound to the conn lease, the
motion_epoch, the payload_epoch and valid_while."""

import json
from pathlib import Path

import jsonschema
import pytest

from keelson import get_subject_schema
from keelson.interfaces_registry import get_procedure_schemas, get_procedures

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "connectors/platform/config-schema.json").read_text())
EXAMPLE = json.loads((ROOT / "connectors/platform/example-config.json").read_text())


@pytest.mark.unit
def test_the_interface_is_vehicle_controls_shape_per_payload():
    assert get_procedures("payload_control", "v1") == [
        "set_payload_mapping",
        "get_payload_mapping",
    ]
    req, resp = get_procedure_schemas("payload_control", "v1", "set_payload_mapping")
    assert {"caller", "payload_id", "axes", "min_interval_s", "max_axis_age_s"} <= set(
        req.fields_by_name
    )
    # The axes reuse vehicle_control/v1's ControlAxis, keyed <axis>/<device-id>: not a copy.
    value = req.fields_by_name["axes"].message_type.fields_by_name["value"].message_type
    assert value.full_name == "keelson.interfaces.vehicle_control.ControlAxis"
    assert {"result", "detail", "payload_epoch", "max_axis_age_s"} <= set(
        resp.fields_by_name
    )


@pytest.mark.unit
def test_the_only_fence_in_a_request_is_the_callers_payload_epoch():
    req, _ = get_procedure_schemas("payload_control", "v1", "set_payload_mapping")
    fenced = [
        f.name
        for f in req.fields
        if f.message_type is not None and f.message_type.full_name == "keelson.Fence"
    ]
    assert fenced == []
    caller = req.fields_by_name["caller"].message_type
    assert caller.full_name == "keelson.interfaces.common.Caller"
    assert caller.fields_by_name["fence"].message_type.full_name == "keelson.Fence"


@pytest.mark.unit
def test_the_clearance_names_every_binding():
    from keelson.payloads.PayloadClearance_pb2 import PayloadClearance

    fields = set(PayloadClearance.DESCRIPTOR.fields_by_name)
    assert {
        "conn_lease_id",
        "motion_epoch",
        "payload_epoch",
        "valid_while",
        "withdrawn",
        "clearance_id",
        "granted_by",
    } <= fields
    valid = PayloadClearance.DESCRIPTOR.fields_by_name["valid_while"].message_type
    assert {
        "min_speed_knots",
        "max_speed_knots",
        "vehicle_modes",
        "max_heel_deg",
    } <= set(valid.fields_by_name)
    # An absent bound is no bound: presence survives (proto3 optional).
    c = PayloadClearance()
    assert not c.valid_while.HasField("max_heel_deg")
    c.valid_while.max_heel_deg = 0.0
    assert PayloadClearance.FromString(c.SerializeToString()).valid_while.HasField(
        "max_heel_deg"
    )


@pytest.mark.unit
def test_it_is_requested_with_command_request_and_stored_per_payload():
    from keelson.payloads.CommandAuthority_pb2 import RequestKind

    assert RequestKind.Value("REQUEST_KIND_PAYLOAD_CLEARANCE") == 5
    assert get_subject_schema("payload_clearance") == "keelson.PayloadClearance"


@pytest.mark.unit
def test_the_example_platform_declares_its_payloads_and_validates():
    jsonschema.validate(EXAMPLE, SCHEMA)
    kinds = {p["id"]: p["payload_safe_state_policy"] for p in EXAMPLE["payloads"]}
    assert kinds == {"gimbal": "HOLD_POSITION", "crane-1": "HOLD_LOAD"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "g",
            "kind": "drone",
            "payload_safe_state_policy": "HOLD_POSITION",
        },  # not a kind
        {"id": "g", "kind": "winch"},  # no safe state: "idle" is not one
        {
            "id": "crane/1",
            "kind": "crane",
            "payload_safe_state_policy": "HOLD_LOAD",
        },  # not one key chunk
        {
            "id": "w",
            "kind": "winch",
            "payload_safe_state_policy": "IDLE",
        },  # not a safe state
        {
            "id": "w",
            "kind": "winch",
            "payload_safe_state_policy": "BRAKE_HOLD",
            "axes": [{"id": "drum/x"}],
        },
    ],
)
def test_a_payload_that_is_not_declarable_is_refused(payload):
    doc = dict(EXAMPLE, payloads=[payload])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)
