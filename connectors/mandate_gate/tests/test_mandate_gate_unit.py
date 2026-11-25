"""The mandate gate's pure core: geometry, the watch, and the hold."""

import json
import math

import pytest

from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover

from mandate_gate.gate import (
    CLEAR,
    HOLDING,
    NO_FIX,
    ConfigError,
    Fix,
    Gate,
    load_config,
)
from mandate_gate.geometry import Plane, Projected, contains
from mandate_gate.watch import Record, Watch, from_json, from_proto, watch_of

pytestmark = pytest.mark.unit

# A square sector east of 11.0°E at 58°N, held by ROC-2: about 5.9 km on a side.
LAT0, LON0 = 58.0, 10.9
PLANE = Plane(LAT0, LON0)
EAST_SECTOR = [[57.97, 11.0], [58.03, 11.0], [58.03, 11.1], [57.97, 11.1]]


def config(**over):
    doc = {
        "initial_watch": "ROC-1",
        "areas": [
            {"id": "east-sector", "roc_sites": ["ROC-2"], "polygon": EAST_SECTOR}
        ],
        "margin_m": 200,
        "loiter_radius_m": 400,
        "reaction_s": 10,
        "stopping": {"speed_knots": 11, "distance_m": 1088},
    }
    doc.update(over)
    return load_config(doc)


def at(east_m, north_m=0.0, cog=90.0, sog=11.0):
    lat, lon = PLANE.to_latlon(east_m, north_m)
    return Fix(lat, lon, cog, sog)


#: The sector's west edge, metres east of the plane's origin.
EDGE_M = PLANE.to_en(LAT0, 11.0)[0]
ROC1 = Watch("ROC-1", "")
ROC2 = Watch("ROC-2", "h-2")


# --- geometry ----------------------------------------------------------------


def test_a_point_is_inside_a_ring_and_not_outside_it():
    ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert contains(ring, (5, 5))
    assert not contains(ring, (15, 5))


def test_the_plane_round_trips():
    lat, lon = PLANE.to_latlon(1234.0, -567.0)
    e, n = PLANE.to_en(lat, lon)
    assert e == pytest.approx(1234.0, abs=1e-6) and n == pytest.approx(-567.0, abs=1e-6)


def test_a_course_into_the_sector_enters_it_at_its_edge():
    c = config()
    p = Projected.of(list(c.areas), LAT0, LON0)
    distance, area = p.first_entry((0.0, 0.0), 90.0, "ROC-1", 20_000)
    assert area.area_id == "east-sector"
    assert distance == pytest.approx(EDGE_M, abs=1.0)


def test_a_course_away_from_the_sector_enters_nothing():
    c = config()
    p = Projected.of(list(c.areas), LAT0, LON0)
    assert p.first_entry((0.0, 0.0), 270.0, "ROC-1", 20_000) is None


def test_the_site_holding_the_sector_may_enter_it():
    c = config()
    p = Projected.of(list(c.areas), LAT0, LON0)
    assert p.first_entry((0.0, 0.0), 90.0, "ROC-2", 20_000) is None


def test_water_outside_every_area_is_open_to_any_watch():
    c = config()
    p = Projected.of(list(c.areas), LAT0, LON0)
    assert p.forbidding((0.0, 0.0), "ROC-9") is None


def test_an_overlap_with_an_area_the_watch_holds_is_a_shared_corridor():
    corridor = [[57.99, 10.99], [58.01, 10.99], [58.01, 11.02], [57.99, 11.02]]
    c = config(
        areas=[
            {"id": "east-sector", "roc_sites": ["ROC-2"], "polygon": EAST_SECTOR},
            {"id": "corridor", "roc_sites": ["ROC-1"], "polygon": corridor},
        ]
    )
    p = Projected.of(list(c.areas), LAT0, LON0)
    inside_both = PLANE.to_en(58.0, 11.01)
    assert p.forbidding(inside_both, "ROC-1") is None
    distance, area = p.first_entry((0.0, 0.0), 90.0, "ROC-1", 20_000)
    assert area.area_id == "east-sector"
    assert distance == pytest.approx(PLANE.to_en(LAT0, 11.02)[0], abs=1.0)


# --- the watch -------------------------------------------------------------------


def record(hid, status="accepted", vessel="alfa", site="ROC-2", accepted=100.0):
    return Record(hid, status, vessel, site, accepted, accepted)


def test_before_any_handover_the_watch_is_the_initial_one():
    assert watch_of({}, "alfa", "ROC-1") == Watch("ROC-1", "")


def test_the_newest_accepted_handover_names_the_watch():
    records = {
        "a": record("a", site="ROC-2", accepted=100.0),
        "b": record("b", site="ROC-3", accepted=200.0),
    }
    assert watch_of(records, "alfa", "ROC-1") == Watch("ROC-3", "b")


@pytest.mark.parametrize(
    "status", ["offered", "pending_vessel", "refused", "cancelled", "expired"]
)
def test_a_handover_that_is_not_accepted_moves_no_watch(status):
    assert watch_of({"a": record("a", status=status)}, "alfa", "ROC-1").site == "ROC-1"


def test_another_vessels_handover_moves_no_watch():
    assert watch_of({"a": record("a", vessel="bravo")}, "alfa", "ROC-1").site == "ROC-1"


def test_a_protobuf_record_reads_its_relief_site():
    h = ChecklistHandover(
        handover_id="h-1", status=ChecklistHandover.HANDOVER_STATUS_ACCEPTED
    )
    h.vessel.entity_id = "alfa"
    h.offered_to.roc_site = " ROC-2 "
    h.accepted_at.FromSeconds(1_000)
    r = from_proto(h)
    assert (
        r.handover_id,
        r.status,
        r.vessel_entity_id,
        r.to_site,
        r.accepted_at_s,
    ) == (
        "h-1",
        "accepted",
        "alfa",
        "ROC-2",
        1000.0,
    )


def test_a_legacy_json_record_reads_its_relief_site():
    raw = json.dumps(
        {
            "handoverId": "h-1",
            "status": "accepted",
            "vessel": {"entityId": "alfa"},
            "to": {"rocSite": "ROC-2"},
            "acceptedAt": "2026-11-25T10:00:00Z",
        }
    ).encode()
    r = from_json(raw)
    assert (r.status, r.vessel_entity_id, r.to_site) == ("accepted", "alfa", "ROC-2")
    assert r.accepted_at_s is not None


def test_bytes_that_are_not_a_json_record_are_not_one():
    assert from_json(b"\x0a\x03abc") is None


# --- the config ------------------------------------------------------------------


@pytest.mark.parametrize(
    "over, words",
    [
        ({"initial_watch": ""}, "initial_watch"),
        ({"areas": []}, "areas"),
        (
            {"areas": [{"id": "a", "roc_sites": [], "polygon": EAST_SECTOR}]},
            "roc_sites",
        ),
        (
            {"areas": [{"id": "a", "roc_sites": ["X"], "polygon": [[0, 0], [1, 1]]}]},
            "polygon",
        ),
        (
            {
                "areas": [
                    {
                        "id": "a",
                        "roc_sites": ["X"],
                        "polygon": [[0, 0], [1, 1], [91, 0]],
                    }
                ]
            },
            "lat, lon",
        ),
        ({"loiter_radius_m": 0}, "loiter_radius_m"),
        ({"margin_m": -1}, "margin_m"),
        ({"surprise": 1}, "unknown"),
    ],
)
def test_a_config_that_cannot_be_right_is_refused(over, words):
    with pytest.raises(ConfigError, match=words):
        config(**over)


def test_two_areas_with_one_id_are_refused():
    area = {"id": "a", "roc_sites": ["X"], "polygon": EAST_SECTOR}
    with pytest.raises(ConfigError, match="twice"):
        config(areas=[area, area])


# --- the hold ----------------------------------------------------------------------


def trigger_m(c, sog=11.0):
    v = sog * 1852 / 3600
    return (
        c.stop_distance_m * sog / c.stop_speed_knots
        + c.margin_m
        + c.loiter_radius_m
        + v * c.reaction_s
    )


def test_far_from_the_line_she_is_clear_and_told_what_is_ahead():
    gate = Gate(config())
    r = gate.update(at(EDGE_M - 4000), ROC1)
    assert r.state == CLEAR and r.hold is None and r.hold_id == 0
    assert r.zone_ahead == "east-sector"
    assert r.distance_to_boundary_m == pytest.approx(4000, abs=2)
    assert "hand the watch over" in r.reason


def test_within_reach_of_the_line_she_is_held_short_of_it():
    c = config()
    gate = Gate(c)
    r = gate.update(at(EDGE_M - trigger_m(c) + 50), ROC1)
    assert r.state == HOLDING and r.hold_id == 1
    centre_e, centre_n = PLANE.to_en(r.hold.lat, r.hold.lon)
    # Short of the line by her stopping distance, the margin, and the radius she may drift in.
    assert centre_e <= EDGE_M - c.stop_distance_m - c.margin_m + 1
    assert EDGE_M - centre_e >= c.loiter_radius_m + c.margin_m - 1
    assert abs(centre_n) < 1
    assert r.hold.radius_m == c.loiter_radius_m


def test_a_hold_is_asked_for_once():
    c = config()
    gate = Gate(c)
    first = gate.update(at(EDGE_M - trigger_m(c) + 50), ROC1)
    again = gate.update(at(EDGE_M - trigger_m(c) + 100, sog=6.0), ROC1)
    assert (
        again.state == HOLDING
        and again.hold_id == first.hold_id
        and again.hold == first.hold
    )


def test_a_ship_already_in_the_sector_is_held_where_she_is():
    gate = Gate(config())
    r = gate.update(at(EDGE_M + 500), ROC1)
    assert r.state == HOLDING and r.distance_to_boundary_m == 0
    e, n = PLANE.to_en(r.hold.lat, r.hold.lon)
    assert math.hypot(e - (EDGE_M + 500), n) < 1
    assert "already in east-sector" in r.reason


def test_a_ship_lying_stopped_is_judged_on_where_she_is_not_where_she_points():
    gate = Gate(config())
    r = gate.update(at(EDGE_M - 50, sog=0.1), ROC1)
    assert r.state == CLEAR and r.distance_to_boundary_m is None


def test_the_watch_reaching_the_site_that_holds_the_sector_clears_her_and_rearms_nothing():
    c = config()
    gate = Gate(c)
    held = gate.update(at(EDGE_M - trigger_m(c) + 50), ROC1)
    r = gate.update(at(EDGE_M - trigger_m(c) + 600, sog=0.2), ROC2)
    assert r.state == CLEAR and r.hold is None
    assert r.hold_id == held.hold_id
    assert "stays in her hold" in r.reason


def test_taken_out_of_her_hold_toward_the_line_she_is_held_again():
    c = config()
    gate = Gate(c)
    first = gate.update(at(EDGE_M - trigger_m(c) + 50), ROC1)
    centre_e, _ = PLANE.to_en(first.hold.lat, first.hold.lon)
    beyond = centre_e + c.loiter_radius_m + c.margin_m + 50
    r = gate.update(at(beyond), ROC1)
    assert r.state == HOLDING and r.hold_id == first.hold_id + 1
    assert "taken out of her hold" in r.reason


def test_taken_out_of_her_hold_away_from_the_line_she_is_clear():
    c = config()
    gate = Gate(c)
    first = gate.update(at(EDGE_M - trigger_m(c) + 50), ROC1)
    centre_e, _ = PLANE.to_en(first.hold.lat, first.hold.lon)
    away = centre_e - c.loiter_radius_m - c.margin_m - 50
    r = gate.update(at(away, cog=270.0), ROC1)
    assert r.state == CLEAR and r.hold is None and r.hold_id == first.hold_id


def test_with_no_fix_she_keeps_her_hold_and_no_new_one_is_asked_for():
    c = config()
    gate = Gate(c)
    first = gate.update(at(EDGE_M - trigger_m(c) + 50), ROC1)
    r = gate.update(None, ROC1)
    assert r.state == NO_FIX and r.hold == first.hold and r.hold_id == first.hold_id


def test_too_close_to_stop_short_she_is_held_where_she_is_and_it_says_so():
    gate = Gate(config())
    r = gate.update(at(EDGE_M - 100), ROC1)
    assert r.state == HOLDING and "too close to stop short" in r.reason
    e, _ = PLANE.to_en(r.hold.lat, r.hold.lon)
    assert e == pytest.approx(EDGE_M - 100, abs=1)


def test_a_hull_that_stops_short_is_held_back_until_her_whole_circle_clears_the_line():
    # Stopping in her own length, the centre would sit a margin short of the
    # line; the circle she may drift about in has to clear it too.
    c = config(stopping={"speed_knots": 11, "distance_m": 0})
    gate = Gate(c)
    south_n = PLANE.to_en(57.97, 11.0)[1]
    r = gate.update(at(EDGE_M + 300, south_n - 640, cog=0.0), ROC1)
    assert r.state == HOLDING and "too close" not in r.reason
    centre = PLANE.to_en(r.hold.lat, r.hold.lon)
    p = Projected.of(list(c.areas), LAT0, LON0)
    assert p.clearance(centre, "ROC-1") >= c.loiter_radius_m + c.margin_m - 1
