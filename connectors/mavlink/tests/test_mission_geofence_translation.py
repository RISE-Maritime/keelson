"""Unit tests for the Keelson <-> MAVLink translation of Mission and
Geofence shapes.

The proto messages are vehicle-agnostic (typed oneof variants); the
connector translates them into MISSION_ITEM_INT-shaped dicts for
pymavlink and back. These tests exercise that mapping directly,
without involving a mav connection or RPC machinery."""

import pytest
from pymavlink.dialects.v20 import ardupilotmega as m

from conftest import mavlink2keelson

from keelson.interfaces.VehicleCommon_pb2 import Coordinate
from keelson.interfaces.VehicleMission_pb2 import (
    ChangeSpeed,
    Delay,
    Loiter,
    Mission,
    MissionItem,
    ReturnHome,
    SetHome,
    Waypoint,
)
from keelson.interfaces.VehicleGeofence_pb2 import (
    Circle,
    FenceZone,
    Geofence,
    Polygon,
)


# ---------------------------------------------------------------------------
# Mission upload (Keelson → wire)
# ---------------------------------------------------------------------------


def _wp(lat, lon, alt=0.0, hold=0.0, radius=0.0, autocontinue=True):
    return MissionItem(
        autocontinue=autocontinue,
        waypoint=Waypoint(
            position=Coordinate(latitude_deg=lat, longitude_deg=lon),
            altitude_m=alt,
            acceptance_radius_m=radius,
            hold_time_s=hold,
        ),
    )


class TestMissionToWire:
    def test_seq_is_synthesised_from_index(self):
        mission = Mission(items=[_wp(1, 2), _wp(3, 4), _wp(5, 6)])
        wire = mavlink2keelson._mission_to_wire(mission)
        assert [w["seq"] for w in wire] == [0, 1, 2]

    def test_waypoint_position_scales_to_dege7(self):
        wire = mavlink2keelson._mission_to_wire(Mission(items=[_wp(59.351, 18.071)]))
        assert wire[0]["x"] == 593510000
        assert wire[0]["y"] == 180710000
        assert wire[0]["command"] == m.MAV_CMD_NAV_WAYPOINT

    def test_waypoint_hold_and_radius_into_params(self):
        wire = mavlink2keelson._mission_to_wire(
            Mission(items=[_wp(0, 0, hold=12.5, radius=3.0)])
        )
        assert wire[0]["param1"] == pytest.approx(12.5)
        assert wire[0]["param2"] == pytest.approx(3.0)

    def test_loiter_unlimited_maps_to_loiter_unlim_cmd(self):
        from google.protobuf.empty_pb2 import Empty

        mission = Mission(
            items=[
                MissionItem(
                    loiter=Loiter(
                        position=Coordinate(latitude_deg=1.0, longitude_deg=2.0),
                        radius_m=10.0,
                        unlimited=Empty(),
                    )
                )
            ]
        )
        wire = mavlink2keelson._mission_to_wire(mission)
        assert wire[0]["command"] == m.MAV_CMD_NAV_LOITER_UNLIM
        assert wire[0]["param3"] == pytest.approx(10.0)

    def test_loiter_turns_and_duration_map_to_distinct_cmds(self):
        mission_turns = Mission(
            items=[
                MissionItem(
                    loiter=Loiter(
                        position=Coordinate(latitude_deg=0, longitude_deg=0),
                        turns=4,
                    )
                )
            ]
        )
        mission_dur = Mission(
            items=[
                MissionItem(
                    loiter=Loiter(
                        position=Coordinate(latitude_deg=0, longitude_deg=0),
                        duration_s=30.0,
                    )
                )
            ]
        )
        assert (
            mavlink2keelson._mission_to_wire(mission_turns)[0]["command"]
            == m.MAV_CMD_NAV_LOITER_TURNS
        )
        assert (
            mavlink2keelson._mission_to_wire(mission_dur)[0]["command"]
            == m.MAV_CMD_NAV_LOITER_TIME
        )

    def test_return_home_carries_no_position(self):
        wire = mavlink2keelson._mission_to_wire(
            Mission(items=[MissionItem(return_home=ReturnHome())])
        )
        assert wire[0]["command"] == m.MAV_CMD_NAV_RETURN_TO_LAUNCH
        assert wire[0]["x"] == 0
        assert wire[0]["y"] == 0

    def test_change_speed_param3_explicit_minus_one(self):
        """ArduPilot interprets param3=-1 as 'no throttle change'.
        Without it, the throttle would be set to the param3 value."""
        wire = mavlink2keelson._mission_to_wire(
            Mission(items=[MissionItem(change_speed=ChangeSpeed(speed_mps=2.5))])
        )
        assert wire[0]["command"] == m.MAV_CMD_DO_CHANGE_SPEED
        assert wire[0]["param2"] == pytest.approx(2.5)
        assert wire[0]["param3"] == pytest.approx(-1.0)

    def test_set_home_carries_position_and_altitude(self):
        wire = mavlink2keelson._mission_to_wire(
            Mission(
                items=[
                    MissionItem(
                        set_home=SetHome(
                            position=Coordinate(latitude_deg=10.0, longitude_deg=20.0),
                            altitude_m=5.0,
                        )
                    )
                ]
            )
        )
        assert wire[0]["command"] == m.MAV_CMD_DO_SET_HOME
        assert wire[0]["z"] == pytest.approx(5.0)

    def test_empty_step_raises(self):
        with pytest.raises(ValueError, match="empty step"):
            mavlink2keelson._mission_to_wire(Mission(items=[MissionItem()]))


# ---------------------------------------------------------------------------
# Mission download (wire → Keelson)
# ---------------------------------------------------------------------------


def _wire_wp(lat_deg, lon_deg, *, hold=0.0, radius=0.0, autocontinue=1):
    return {
        "seq": 0,
        "frame": m.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        "command": m.MAV_CMD_NAV_WAYPOINT,
        "current": 0,
        "autocontinue": autocontinue,
        "param1": hold,
        "param2": radius,
        "param3": 0.0,
        "param4": 0.0,
        "x": int(round(lat_deg * 1e7)),
        "y": int(round(lon_deg * 1e7)),
        "z": 0.0,
        "mission_type": 0,
    }


class TestWireToMission:
    def test_waypoint_round_trip(self):
        wire = [_wire_wp(59.351, 18.071, hold=2.0, radius=5.0)]
        mission = mavlink2keelson._wire_to_mission(wire)
        assert len(mission.items) == 1
        item = mission.items[0]
        assert item.WhichOneof("step") == "waypoint"
        assert item.waypoint.position.latitude_deg == pytest.approx(59.351, rel=1e-6)
        assert item.waypoint.position.longitude_deg == pytest.approx(18.071, rel=1e-6)
        assert item.waypoint.hold_time_s == pytest.approx(2.0)
        assert item.waypoint.acceptance_radius_m == pytest.approx(5.0)

    def test_unsupported_command_raises(self):
        # MAV_CMD_NAV_TAKEOFF (22) isn't in the supported set — should be a
        # clear error rather than a silently-wrong default mapping.
        wire = _wire_wp(0, 0)
        wire["command"] = m.MAV_CMD_NAV_TAKEOFF
        with pytest.raises(ValueError, match="unsupported MAV_CMD"):
            mavlink2keelson._wire_to_mission([wire])

    def test_loiter_three_variants(self):
        wires = [
            {
                **_wire_wp(0, 0),
                "command": m.MAV_CMD_NAV_LOITER_UNLIM,
                "param3": 7.0,
            },
            {
                **_wire_wp(0, 0),
                "command": m.MAV_CMD_NAV_LOITER_TURNS,
                "param1": 3.0,
                "param3": 7.0,
            },
            {
                **_wire_wp(0, 0),
                "command": m.MAV_CMD_NAV_LOITER_TIME,
                "param1": 15.5,
                "param3": 7.0,
            },
        ]
        mission = mavlink2keelson._wire_to_mission(wires)
        terms = [it.loiter.WhichOneof("termination") for it in mission.items]
        assert terms == ["unlimited", "turns", "duration_s"]
        assert mission.items[1].loiter.turns == 3
        assert mission.items[2].loiter.duration_s == pytest.approx(15.5)


class TestMissionRoundTrip:
    def test_full_round_trip_preserves_typed_step(self):
        """Build a mission with one of every step type, send it through
        upload-then-download, and verify the typed shape survives."""
        from google.protobuf.empty_pb2 import Empty

        original = Mission(
            items=[
                _wp(1.0, 2.0, hold=1.5, radius=3.0),
                MissionItem(
                    loiter=Loiter(
                        position=Coordinate(latitude_deg=4, longitude_deg=5),
                        radius_m=2.0,
                        unlimited=Empty(),
                    )
                ),
                MissionItem(delay=Delay(duration_s=10.0)),
                MissionItem(return_home=ReturnHome()),
                MissionItem(change_speed=ChangeSpeed(speed_mps=1.5)),
                MissionItem(
                    set_home=SetHome(
                        position=Coordinate(latitude_deg=7, longitude_deg=8),
                        altitude_m=1.0,
                    )
                ),
            ]
        )
        wire = mavlink2keelson._mission_to_wire(original)
        roundtrip = mavlink2keelson._wire_to_mission(wire)
        steps = [it.WhichOneof("step") for it in roundtrip.items]
        assert steps == [
            "waypoint",
            "loiter",
            "delay",
            "return_home",
            "change_speed",
            "set_home",
        ]


# ---------------------------------------------------------------------------
# Geofence upload (Keelson → wire)
# ---------------------------------------------------------------------------


class TestGeofenceToWire:
    def test_polygon_fans_into_one_item_per_vertex(self):
        g = Geofence(
            zones=[
                FenceZone(
                    kind=FenceZone.INCLUSION,
                    polygon=Polygon(
                        vertices=[
                            Coordinate(latitude_deg=1, longitude_deg=2),
                            Coordinate(latitude_deg=3, longitude_deg=4),
                            Coordinate(latitude_deg=5, longitude_deg=6),
                        ]
                    ),
                )
            ]
        )
        wire = mavlink2keelson._geofence_to_wire(g)
        assert len(wire) == 3
        assert all(
            w["command"] == m.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION for w in wire
        )
        # param1 = vertex count on every item — ArduPilot uses this to
        # delimit polygons in the flat list.
        assert all(w["param1"] == pytest.approx(3.0) for w in wire)
        # All items share mission_type=1 (FENCE).
        assert all(w["mission_type"] == 1 for w in wire)
        # Sequence numbers are dense from 0.
        assert [w["seq"] for w in wire] == [0, 1, 2]

    def test_exclusion_polygon_uses_exclusion_cmd(self):
        g = Geofence(
            zones=[
                FenceZone(
                    kind=FenceZone.EXCLUSION,
                    polygon=Polygon(
                        vertices=[Coordinate(latitude_deg=0, longitude_deg=0)] * 3
                    ),
                )
            ]
        )
        wire = mavlink2keelson._geofence_to_wire(g)
        assert all(
            w["command"] == m.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION for w in wire
        )

    def test_circle_inclusion_and_exclusion(self):
        g = Geofence(
            zones=[
                FenceZone(
                    kind=FenceZone.INCLUSION,
                    circle=Circle(
                        center=Coordinate(latitude_deg=1, longitude_deg=2),
                        radius_m=50.0,
                    ),
                ),
                FenceZone(
                    kind=FenceZone.EXCLUSION,
                    circle=Circle(
                        center=Coordinate(latitude_deg=3, longitude_deg=4),
                        radius_m=25.0,
                    ),
                ),
            ]
        )
        wire = mavlink2keelson._geofence_to_wire(g)
        assert len(wire) == 2
        assert wire[0]["command"] == m.MAV_CMD_NAV_FENCE_CIRCLE_INCLUSION
        assert wire[0]["param1"] == pytest.approx(50.0)
        assert wire[1]["command"] == m.MAV_CMD_NAV_FENCE_CIRCLE_EXCLUSION
        assert wire[1]["param1"] == pytest.approx(25.0)

    def test_return_point_emitted_first_when_present(self):
        g = Geofence(
            return_point=Coordinate(latitude_deg=9, longitude_deg=10),
            zones=[
                FenceZone(
                    kind=FenceZone.INCLUSION,
                    circle=Circle(
                        center=Coordinate(latitude_deg=0, longitude_deg=0),
                        radius_m=1.0,
                    ),
                )
            ],
        )
        wire = mavlink2keelson._geofence_to_wire(g)
        assert wire[0]["command"] == m.MAV_CMD_NAV_FENCE_RETURN_POINT
        assert wire[1]["command"] == m.MAV_CMD_NAV_FENCE_CIRCLE_INCLUSION

    def test_empty_polygon_raises(self):
        g = Geofence(
            zones=[FenceZone(kind=FenceZone.INCLUSION, polygon=Polygon(vertices=[]))]
        )
        with pytest.raises(ValueError, match="no vertices"):
            mavlink2keelson._geofence_to_wire(g)

    def test_empty_shape_raises(self):
        g = Geofence(zones=[FenceZone(kind=FenceZone.INCLUSION)])
        with pytest.raises(ValueError, match="empty shape"):
            mavlink2keelson._geofence_to_wire(g)
