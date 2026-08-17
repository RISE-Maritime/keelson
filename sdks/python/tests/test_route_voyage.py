"""Tests for the route planning and voyage contracts (protocol-specification.md §6).

These cover the rules §6 makes that a `.proto` file cannot state on its own, and
the removals that would otherwise only be enforced by review: that a reference is
a pair, that geometry is exclusive, and that the fields §6.8 decided to remove
stay gone rather than quietly reappearing under their old numbers.
"""

import keelson

from google.protobuf import descriptor_pb2
from google.protobuf.descriptor import FieldDescriptor

from keelson.payloads.Route_pb2 import (
    ActionPoint,
    DefaultWaypoint,
    HazardZone,
    Route,
    RouteInfo,
    RouteRef,
    RouteSignature,
    RouteSignatures,
    ScheduleElement,
    Waypoint,
)
from keelson.payloads.foxglove.GeoJSON_pb2 import GeoJSON
from keelson.payloads.RouteChangeEvent_pb2 import RouteChangeEvent
from keelson.payloads.RouteExecution_pb2 import RouteExecution
from keelson.payloads.Voyage_pb2 import Voyage


ROUTE_SUBJECTS = {
    "route": "keelson.Route",
    "route_status": "keelson.RouteStatusUpdate",
    "voyage": "keelson.Voyage",
    "route_execution": "keelson.RouteExecution",
    "route_edit_authority": "keelson.RouteEditAuthority",
    "route_edit_request": "keelson.RouteEditRequest",
    "route_change_event": "keelson.RouteChangeEvent",
    "route_signature": "keelson.RouteSignatures",
}


def _field_names(message_class):
    return {field.name for field in message_class.DESCRIPTOR.fields}


def test_route_subjects_resolve_to_their_types():
    for subject, type_name in ROUTE_SUBJECTS.items():
        assert keelson.is_subject_well_known(subject)
        assert keelson.get_subject_schema(subject) == type_name


def test_route_ref_survives_an_envelope_round_trip():
    """A RouteRef must arrive as the pair it left as — §6.2."""
    ref = RouteRef(route_id="0f8b-route", route_edition_number=7)
    voyage = Voyage(voyage_id="v-1", route_ref=ref)

    envelope = keelson.enclose(voyage.SerializeToString())
    _received_at, _enclosed_at, payload = keelson.uncover(envelope)
    decoded = Voyage.FromString(payload)

    assert decoded.route_ref.route_id == "0f8b-route"
    assert decoded.route_ref.route_edition_number == 7


def test_both_pinning_messages_carry_a_route_ref_not_loose_fields():
    """Voyage and RouteExecution pin an edition the same way — §6.2."""
    for message_class in (Voyage, RouteExecution):
        names = _field_names(message_class)
        assert "route_ref" in names
        assert "route_id" not in names
        assert "route_edition_number" not in names


def test_route_execution_addresses_waypoints_by_id_not_index():
    """Array indices mean a different waypoint after an edit — §6.2."""
    names = _field_names(RouteExecution)
    assert {"current_leg_from_waypoint_id", "next_waypoint_id"} <= names
    assert "current_leg_index" not in names
    assert "next_waypoint_index" not in names


def test_waypoint_overrides_are_distinguishable_from_zero():
    """`Waypoints.defaults` backs these fields, so presence is load-bearing.

    Without `optional`, a waypoint that simply does not override the route's
    default speed serializes identically to one commanding a dead stop —
    and 0.0 is the more dangerous reading of the pair.
    """
    for field in (
        "planned_sog_knots",
        "radius_m",
        "rate_of_turn_degps",
        "wheel_over_distance_m",
    ):
        unset = Waypoint(id="WP-1")
        explicit_zero = Waypoint(id="WP-1")
        setattr(explicit_zero, field, 0.0)

        assert not unset.HasField(field), f"{field} should start unset"
        assert explicit_zero.HasField(field), f"{field} set to 0 should be present"
        assert (
            unset.SerializeToString() != explicit_zero.SerializeToString()
        ), f"Waypoint.{field}: unset and explicit 0 are the same bytes"

    # Same ambiguity one level up: "this route sets no default speed" must be
    # distinguishable from "this route defaults to zero".
    for field in ("radius_m", "planned_sog_knots"):
        unset = DefaultWaypoint()
        explicit_zero = DefaultWaypoint()
        setattr(explicit_zero, field, 0.0)
        assert (
            unset.SerializeToString() != explicit_zero.SerializeToString()
        ), f"DefaultWaypoint.{field}: unset and explicit 0 are the same bytes"


def test_every_id_in_the_route_family_is_a_string():
    """§6.2 rests its addressing rule on `Waypoint.id`, so the odd one out sat
    in the worst place. RTZ numbers its waypoints; keelson does not follow it
    there — see the field comment for the trade-off."""
    assert Waypoint.DESCRIPTOR.fields_by_name["id"].type == FieldDescriptor.TYPE_STRING

    # Everything that refers to a waypoint must agree with it.
    referrers = {
        ScheduleElement: "waypoint_id",
        RouteExecution: "current_leg_from_waypoint_id",
    }
    for message_class, field_name in referrers.items():
        field = message_class.DESCRIPTOR.fields_by_name[field_name]
        assert (
            field.type == FieldDescriptor.TYPE_STRING
        ), f"{message_class.DESCRIPTOR.name}.{field_name} still expects an integer id"


def test_action_point_geometry_is_exclusive():
    """Setting one member of the oneof clears the other."""
    action_point = ActionPoint(area_geojson=GeoJSON(geojson='{"type":"Polygon"}'))
    assert action_point.WhichOneof("geometry") == "area_geojson"

    action_point.circle.radius_m = 50.0
    assert action_point.WhichOneof("geometry") == "circle"
    assert action_point.area_geojson.geojson == ""


def test_hazard_zone_geometry_is_exclusive():
    hazard = HazardZone(polygon_geojson=GeoJSON(geojson='{"type":"Polygon"}'))
    assert hazard.WhichOneof("geometry") == "polygon_geojson"

    hazard.circle.radius_m = 120.0
    assert hazard.WhichOneof("geometry") == "circle"
    assert hazard.polygon_geojson.geojson == ""


def test_signatures_live_beside_the_edition_not_inside_it():
    """§6.3.1 — a signature list inside the document it signs is circular."""
    assert "signatures" not in _field_names(Route)

    signed = RouteSignatures(
        route_ref=RouteRef(route_id="0f8b-route", route_edition_number=7),
        signatures=[RouteSignature(signer_id="master", algorithm="ed25519")],
    )
    decoded = RouteSignatures.FromString(signed.SerializeToString())

    assert decoded.route_ref.route_edition_number == 7
    assert decoded.signatures[0].algorithm == "ed25519"


def test_there_is_exactly_one_audit_channel():
    """§6.4 — RouteChangeEvent is the truth; the embedded history is gone."""
    route_info_field = Route.DESCRIPTOR.fields_by_name["info"]
    assert "change_history" not in {
        field.name for field in route_info_field.message_type.fields
    }
    assert "change_summary" in _field_names(RouteChangeEvent)


def _reserved(message_class):
    """Reserved field numbers and names, which only the DescriptorProto carries."""
    proto = descriptor_pb2.DescriptorProto()
    message_class.DESCRIPTOR.CopyToProto(proto)
    numbers = {
        number
        for entry in proto.reserved_range
        for number in range(entry.start, entry.end)
    }
    return numbers, set(proto.reserved_name)


def test_removed_fields_stay_removed():
    """Every removal reserves its number AND its name, so neither can be reused.

    A number reserved without its name lets a later field silently take the old
    name back, which is the confusing half of the mistake.
    """
    expected = {
        Route: ({21, 30}, {"rerouting_policy", "signatures"}),
        RouteInfo: ({60}, {"change_history"}),
        RouteChangeEvent: ({10}, {"diff"}),
        RouteExecution: (
            {3, 4, 10, 11},
            {
                "route_id",
                "route_edition_number",
                "current_leg_index",
                "next_waypoint_index",
            },
        ),
        Voyage: ({3, 4}, {"route_id", "route_edition_number"}),
        ActionPoint: ({3, 4, 5}, {"position", "radius_m", "area"}),
        HazardZone: ({10, 11, 12}, {"centre", "radius_m", "polygon"}),
    }

    for message_class, (numbers, names) in expected.items():
        name = message_class.DESCRIPTOR.name
        reserved_numbers, reserved_names = _reserved(message_class)

        assert numbers <= reserved_numbers, (
            f"{name}: expected {sorted(numbers)} reserved, "
            f"got {sorted(reserved_numbers)}"
        )
        assert names <= reserved_names, (
            f"{name}: expected {sorted(names)} reserved by name, "
            f"got {sorted(reserved_names)}"
        )

        live = {field.number for field in message_class.DESCRIPTOR.fields}
        assert not (numbers & live), f"{name} reused a reserved number"
