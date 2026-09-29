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
                "kinematics": {
                    "position": {"latitude_deg": 57.70, "longitude_deg": 11.9},
                    "course_over_ground_deg": 0.0,
                },
            }
        ],
        "own_ship": {"position": {"latitude_deg": 57.69, "longitude_deg": 11.9}},
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
    kin = decoded.targets[0].kinematics
    assert kin.position.latitude_deg == 57.70
    assert kin.HasField("course_over_ground_deg") and kin.course_over_ground_deg == 0.0
    assert not kin.HasField("heading_deg"), "an unknown heading is absent, not north"
    assert decoded.HasField("own_ship")


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


def test_the_geometry_subject_is_timestamped_geojson():
    """The reachable set and escape path travel beside the record, own-motion geometry only."""
    assert keelson.is_subject_well_known("navigation_risk_geometry")
    assert keelson.get_subject_schema("navigation_risk_geometry") == "keelson.TimestampedGeoJSON"


@pytest.mark.parametrize(
    "message, field",
    [
        (NavigationRisk.DimensionRisk, "reachable_blocked_share"),
        (NavigationRisk.TurnEnvelope, "radius_m"),
        (NavigationRisk.TurnEnvelope, "horizon_s"),
        (NavigationRisk.EscapeWitness, "min_target_clearance_m"),
        (NavigationRisk.EncounterConduct, "applicable"),
        (NavigationRisk.PredicateState, "value"),
    ],
)
def test_added_fields_keep_presence(message, field):
    """Unknown is not zero here either: an undecided applicability is not 'not applicable'."""
    unset = message.FromString(message().SerializeToString())
    assert not unset.HasField(field)
    zero = message()
    setattr(zero, field, 0)
    assert message.FromString(zero.SerializeToString()).HasField(field)


def test_gates_turn_envelope_escape_and_conduct_round_trip_through_json():
    record = {
        "combined": {"level": "LEVEL_LOW"},
        "gates": {
            "applied": 1, "pass": False,
            "not_applied": ["DIMENSION_GROUNDING", "DIMENSION_MANOEUVRE"],
            "not_declared": ["DIMENSION_MANOEUVRE"],
            "checks": [{"dimension": "DIMENSION_FAIRWAY", "value": 0.75, "limit": 0.5, "pass": False}],
        },
        "dimensions": [{
            "dimension": "DIMENSION_MANOEUVRE", "measurable": True, "risk": 0.0,
            "provenance": "PROVENANCE_ASSUMED", "reachable_blocked_share": 0.0,
            "escape": {"measurable": True, "found": True, "manoeuvre": "starboard 60°",
                       "checked": 3, "blocked": 2, "flags": ["chart_not_checked"]},
        }],
        "inputs": {
            "clock_basis": "CLOCK_BASIS_SOURCE",
            "turn_envelope": {"basis": "PROVENANCE_SYNTHESISED", "radius_m": 15.0,
                              "omega_dps": 10.5, "horizon_s": 64.0, "flags": ["lag_not_modelled"]},
        },
        "own_ship": {"sog_basis": "reported", "cog_basis": "reported"},
        "conduct": {
            "rolling": True, "review_source": "conduct_review.yaml",
            "encounters": [{
                "encounter_id": "265101001@2026-10-06T06:43:10Z", "target_id": "tg265101001",
                "mmsi": 265101001, "onset": "2026-10-06T06:43:10Z", "provisional": True,
                "applicable": True, "role": "ROLE_GIVE_WAY", "role_basis": "rule_15",
                "role_established": True, "visibility_regime": "in_sight",
                "state": "CONDUCT_STATE_CONSISTENT",
                "predicates": [{"id": "C1", "rule": "8(a), 16", "state": "CONDUCT_STATE_CONSISTENT",
                                "value": 380.5, "threshold": {"name": "early_tcpa_s", "value": 240,
                                                              "source": "review", "reviewer": "r"}}],
                "gates": [{"id": "G1", "state": "GATE_STATE_NOT_FIRED"}],
                "flags": ["rule_2_not_excluded", "lookout_not_evidenced"],
            }],
        },
    }
    msg = json_format.ParseDict(record, NavigationRisk())
    back = json_format.MessageToDict(msg, preserving_proto_field_name=True)
    assert back["gates"]["checks"][0]["limit"] == 0.5
    assert back["dimensions"][0]["escape"]["manoeuvre"] == "starboard 60°"
    assert back["inputs"]["turn_envelope"]["radius_m"] == 15.0
    assert back["conduct"]["encounters"][0]["predicates"][0]["threshold"]["name"] == "early_tcpa_s"
    # An undecided applicability survives as absent, not False.
    undecided = NavigationRisk.EncounterConduct()
    assert not NavigationRisk.EncounterConduct.FromString(undecided.SerializeToString()).HasField("applicable")
