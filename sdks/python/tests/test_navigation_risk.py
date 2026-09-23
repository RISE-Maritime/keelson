"""Tests for navigation risk: the `navigation_risk` subject and `navigation_risk_control/v1`.

The subject's one load-bearing rule is that an absent risk is not zero. A dimension whose inputs
are missing is published as not measurable, and a consumer that reads it as 0.0 reports the
vessel as safe on exactly the occasions it cannot tell. `risk` is `optional` so that presence
survives the wire; these tests pin that, because a later edit dropping the keyword would still
compile, still round-trip, and silently turn "not measurable" into "no risk".
"""

import keelson
import pytest
from google.protobuf import json_format

from keelson import qos
from keelson.interfaces import get_procedure_schemas, get_procedures
from keelson.payloads.NavigationRisk_pb2 import NavigationRisk


def test_the_subject_resolves_and_travels_default():
    assert keelson.is_subject_well_known("navigation_risk")
    assert keelson.get_subject_schema("navigation_risk") == "keelson.NavigationRisk"
    # Restated every interval: a lost sample is replaced a second later.
    assert qos.profile_name_for("navigation_risk") == "default"


@pytest.mark.parametrize(
    "message, field",
    [
        (NavigationRisk.DimensionRisk, "risk"),
        (NavigationRisk.TargetRisk, "risk"),
        (NavigationRisk.TargetRisk, "level"),
        (NavigationRisk.TargetRisk, "dcpa_m"),
        (NavigationRisk.TargetRisk, "tcpa_s"),
    ],
)
def test_risk_fields_keep_presence(message, field):
    """Unset and 0.0 must be distinguishable after a round trip."""
    unset = message.FromString(message().SerializeToString())
    assert not unset.HasField(field)

    zero = message()
    setattr(zero, field, 0)
    zero = message.FromString(zero.SerializeToString())
    assert zero.HasField(field)
    assert getattr(zero, field) == 0


def test_a_record_round_trips_through_protobuf_json():
    """The first producer builds the record as protobuf JSON; it must parse into the message."""
    record = {
        "timestamp": "2026-09-23T12:00:00Z",
        "evaluated_at": "2026-09-23T12:00:00.250Z",
        "model": {"package": "navrisk", "version": "0.2.0"},
        "combined": {
            "risk_combined": 0.12,
            "scored_coverage": 0.6,
            "ceiling": 0.8,
            "risk_fraction": 0.15,
            "level": "LEVEL_LOW",
        },
        "gates": {"applied": 1, "not_applied": ["DIMENSION_GROUNDING"], "pass": True},
        "dimensions": [
            {
                "dimension": "DIMENSION_COLLISION",
                "measurable": True,
                "risk": 0.0,
                "provenance": "PROVENANCE_AIS",
            },
            {
                "dimension": "DIMENSION_GROUNDING",
                "measurable": False,
                "provenance": "PROVENANCE_ABSENT",
                "note": "no chart database",
            },
        ],
        "targets": [
            {
                "target_id": "tg265000001",
                "mmsi": 265000001,
                "feed": "srv-herakles/sjofartsverket",
                "measurable": True,
                "risk": 0.02,
                "level": "LEVEL_LOW",
                "dcpa_m": 850.0,
                "tcpa_s": 420.0,
            }
        ],
        "inputs": {"targets_tracked": 1, "targets_out_of_range": 5321},
    }
    msg = json_format.ParseDict(record, NavigationRisk())
    decoded = NavigationRisk.FromString(msg.SerializeToString())

    collision, grounding = decoded.dimensions
    assert collision.HasField("risk") and collision.risk == 0.0
    assert not grounding.HasField("risk")
    assert decoded.targets[0].feed == "srv-herakles/sjofartsverket"
    assert decoded.targets[0].level == NavigationRisk.LEVEL_LOW
    assert decoded.inputs.targets_out_of_range == 5321


def test_not_assessed_is_not_ordinal():
    """LEVEL_NOT_ASSESSED sorts above HIGH: a consumer must compare by name."""
    assert NavigationRisk.LEVEL_NOT_ASSESSED > NavigationRisk.LEVEL_HIGH


def test_control_interface_procedures():
    # Declaration order is load-bearing: crowsnest's scripts/checks/navigationRisk.mjs pins the
    # same list.
    assert get_procedures("navigation_risk_control", "v1") == [
        "list_assessments",
        "start_assessment",
        "stop_assessment",
    ]
    req, resp = get_procedure_schemas(
        "navigation_risk_control", "v1", "start_assessment"
    )
    assert req.full_name == (
        "keelson.interfaces.navigation_risk_control.StartAssessmentRequest"
    )
    assert resp.full_name == (
        "keelson.interfaces.navigation_risk_control.StartAssessmentResponse"
    )


def test_own_ship_particulars_keep_presence():
    """A missing draught makes grounding not measurable; it must not arrive as 0 m."""
    from keelson.interfaces.NavigationRiskControl_pb2 import OwnShipParticulars

    decoded = OwnShipParticulars.FromString(
        OwnShipParticulars(length_over_all_m=6.5).SerializeToString()
    )
    assert decoded.HasField("length_over_all_m")
    assert not decoded.HasField("draught_max_m")
