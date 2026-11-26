"""keelson#465: the shared conn log, a decline on the wire, a ranked forced takeover, and the
areas where a vessel may operate without a ROC."""

import keelson
from keelson.payloads.CommandAuthority_pb2 import CommandAuthority, CommandRequest
from keelson.payloads.ConnEvent_pb2 import ConnEvent
from keelson.payloads.Route_pb2 import Route


def test_conn_event_is_a_registered_subject():
    assert keelson.get_subject_schema("conn_event") == "keelson.ConnEvent"


def test_a_conn_event_records_who_held_her_and_why_she_was_lost():
    e = ConnEvent(
        vessel_id="alfa",
        event_id="ce_1",
        kind=ConnEvent.KIND_TAKEN_AFTER_LOSS,
        holder=ConnEvent.Party(
            controller_id="anna@ROC-SWE", site="ROC-SWE", role="navigation-officer"
        ),
        previous_holder=ConnEvent.Party(
            controller_id="ted@ROC-TED", site="ROC-TED", role="master"
        ),
        cause=ConnEvent.CAUSE_HOLDER_STATION_SILENT,
        recorded_by=ConnEvent.Party(controller_id="anna@ROC-SWE", site="ROC-SWE"),
    )
    e.timestamp.FromSeconds(1_700_000_100)
    e.holder_station_last_heard.FromSeconds(1_700_000_000)
    back = ConnEvent.FromString(e.SerializeToString())
    assert (back.kind, back.cause) == (4, 3)
    assert (back.previous_holder.controller_id, back.previous_holder.role) == (
        "ted@ROC-TED",
        "master",
    )
    assert back.holder_station_last_heard.seconds == 1_700_000_000


def test_a_release_can_permit_her_to_go_on_without_a_roc():
    e = ConnEvent(kind=ConnEvent.KIND_RELEASED)
    e.unattended_permit.to_end_of_route = True
    e.unattended_permit.route_id = "route_1"
    back = ConnEvent.FromString(e.SerializeToString())
    assert back.unattended_permit.WhichOneof("until") == "to_end_of_route"
    assert back.unattended_permit.route_id == "route_1"


def test_the_kinds_and_causes_are_numbered_as_proposed():
    assert [ConnEvent.Kind.Name(i) for i in range(7)] == [
        "KIND_UNSPECIFIED",
        "KIND_TAKEN",
        "KIND_GRANTED",
        "KIND_RELEASED",
        "KIND_TAKEN_AFTER_LOSS",
        "KIND_FORCED_TAKEOVER",
        "KIND_REQUEST_DECLINED",
    ]
    assert [ConnEvent.Cause.Name(i) for i in range(4)] == [
        "CAUSE_UNSPECIFIED",
        "CAUSE_RELEASED",
        "CAUSE_LOST_LINK",
        "CAUSE_HOLDER_STATION_SILENT",
    ]


def test_a_lease_names_its_role_and_says_when_it_was_forced():
    a = CommandAuthority(
        vessel_id="alfa",
        controller_id="ted@ROC-TED",
        controller_role="master",
        forced=True,
        forced_reason="no answer for 30 s",
    )
    back = CommandAuthority.FromString(a.SerializeToString())
    assert (back.controller_role, back.forced, back.forced_reason) == (
        "master",
        True,
        "no answer for 30 s",
    )
    # A lease from before the fields: no role, not forced.
    old = CommandAuthority.FromString(
        CommandAuthority(vessel_id="alfa").SerializeToString()
    )
    assert (old.controller_role, old.forced) == ("", False)


def test_a_request_carries_its_role_and_can_be_declined():
    r = CommandRequest(
        vessel_id="alfa",
        requester_id="anna",
        requester_site="ROC-SWE",
        requester_role="fleet-watch-operator",
        declined=True,
        declined_by="ted",
        declined_by_site="ROC-TED",
        decline_reason="mid-manoeuvre",
    )
    back = CommandRequest.FromString(r.SerializeToString())
    assert back.requester_role == "fleet-watch-operator"
    assert (back.declined, back.declined_by, back.decline_reason) == (
        True,
        "ted",
        "mid-manoeuvre",
    )


def test_a_route_carries_areas_where_she_may_operate_without_a_roc():
    route = Route()
    area = route.unattended_areas.add(id="ua_1", description="open sea leg")
    area.circle.centre.latitude = 57.6
    area.circle.centre.longitude = 11.4
    area.circle.radius_m = 2000
    back = Route.FromString(route.SerializeToString())
    assert back.unattended_areas[0].id == "ua_1"
    assert back.unattended_areas[0].WhichOneof("geometry") == "circle"
    assert not back.hazard_zones  # a permission, not a hazard
