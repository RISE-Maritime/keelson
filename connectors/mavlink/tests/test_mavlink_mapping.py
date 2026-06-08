"""Pure mapping function tests for mavlink2keelson.

Each test constructs a synthetic MAVLink message via pymavlink's message
classes, runs the mapper, and asserts the resulting subjects/payloads.
No Zenoh, no I/O.
"""

import math

import pytest
from pymavlink.dialects.v20 import ardupilotmega as m

from conftest import mavlink2keelson as mk

import keelson
from keelson.payloads.Decomposed3DVector_pb2 import Decomposed3DVector
from keelson.payloads.EntityHealth_pb2 import EntityHealth, HealthLevel
from keelson.payloads.Primitives_pb2 import (
    TimestampedBool,
    TimestampedFloat,
    TimestampedInt,
    TimestampedQuaternion,
    TimestampedString,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality


TS = 1_700_000_000_000_000_000  # fixed nanosecond timestamp for determinism


def _decode(envelope_bytes, message_class):
    _recv, enclosed_at, payload_bytes = keelson.uncover(envelope_bytes)
    msg = message_class()
    msg.ParseFromString(payload_bytes)
    return enclosed_at, msg


# ---------------------------------------------------------------------------
# HEARTBEAT
# ---------------------------------------------------------------------------


def _build_heartbeat(armed=True, mode=m.MAV_STATE_ACTIVE, custom_mode=10):
    base = m.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0
    return m.MAVLink_heartbeat_message(
        type=m.MAV_TYPE_SURFACE_BOAT,
        autopilot=m.MAV_AUTOPILOT_ARDUPILOTMEGA,
        base_mode=base | m.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        custom_mode=custom_mode,
        system_status=mode,
        mavlink_version=3,
    )


class TestHeartbeat:
    def test_emits_three_subjects(self):
        out = list(mk.map_heartbeat(_build_heartbeat(), TS))
        subjects = [s for s, _, _ in out]
        assert subjects == ["vehicle_mode", "vehicle_armed", "entity_health"]

    def test_armed_bool(self):
        out = dict(
            (s, env) for s, _, env in mk.map_heartbeat(_build_heartbeat(armed=True), TS)
        )
        _, armed = _decode(out["vehicle_armed"], TimestampedBool)
        assert armed.value is True

        out = dict(
            (s, env)
            for s, _, env in mk.map_heartbeat(_build_heartbeat(armed=False), TS)
        )
        _, armed = _decode(out["vehicle_armed"], TimestampedBool)
        assert armed.value is False

    def test_active_state_maps_to_nominal(self):
        out = dict(
            (s, env)
            for s, _, env in mk.map_heartbeat(
                _build_heartbeat(mode=m.MAV_STATE_ACTIVE), TS
            )
        )
        _, eh = _decode(out["entity_health"], EntityHealth)
        assert eh.level == HealthLevel.HEALTH_NOMINAL

    def test_critical_state_maps_to_critical(self):
        out = dict(
            (s, env)
            for s, _, env in mk.map_heartbeat(
                _build_heartbeat(mode=m.MAV_STATE_CRITICAL), TS
            )
        )
        _, eh = _decode(out["entity_health"], EntityHealth)
        assert eh.level == HealthLevel.HEALTH_CRITICAL

    def test_emergency_state_maps_to_critical(self):
        out = dict(
            (s, env)
            for s, _, env in mk.map_heartbeat(
                _build_heartbeat(mode=m.MAV_STATE_EMERGENCY), TS
            )
        )
        _, eh = _decode(out["entity_health"], EntityHealth)
        assert eh.level == HealthLevel.HEALTH_CRITICAL

    def test_envelope_carries_timestamp(self):
        out = list(mk.map_heartbeat(_build_heartbeat(), TS))
        for _, _, env in out:
            enclosed_at, _ = _decode(env, TimestampedString)
            # Envelope's enclosed_at is independent of TS arg (it's set inside
            # the helpers from time.time_ns() at enclose time). We just check
            # it's a positive integer.
            assert enclosed_at > 0


# ---------------------------------------------------------------------------
# SYS_STATUS
# ---------------------------------------------------------------------------


def _build_sys_status(enabled_bits=0, healthy_bits=0, present_bits=None):
    # present_bits defaults to enabled_bits (the common case: a sensor that is
    # enabled is also present). Pass it explicitly to model "configured but
    # disabled" subsystems — e.g. a geofence present but not enforcing.
    if present_bits is None:
        present_bits = enabled_bits
    return m.MAVLink_sys_status_message(
        onboard_control_sensors_present=present_bits,
        onboard_control_sensors_enabled=enabled_bits,
        onboard_control_sensors_health=healthy_bits,
        load=500,
        voltage_battery=12000,
        current_battery=-1,
        battery_remaining=-1,
        drop_rate_comm=0,
        errors_comm=0,
        errors_count1=0,
        errors_count2=0,
        errors_count3=0,
        errors_count4=0,
    )


class TestSysStatus:
    def test_emits_entity_health(self):
        out = list(
            mk.map_sys_status(
                _build_sys_status(
                    enabled_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                    healthy_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                ),
                TS,
            )
        )
        assert [s for s, _, _ in out] == ["entity_health"]

    def test_nominal_when_all_enabled_bits_healthy(self):
        bits = (
            m.MAV_SYS_STATUS_SENSOR_3D_GYRO
            | m.MAV_SYS_STATUS_SENSOR_3D_ACCEL
            | m.MAV_SYS_STATUS_SENSOR_3D_MAG
        )
        out = dict(
            (s, env)
            for s, _, env in mk.map_sys_status(
                _build_sys_status(enabled_bits=bits, healthy_bits=bits), TS
            )
        )
        _, eh = _decode(out["entity_health"], EntityHealth)
        assert eh.level == HealthLevel.HEALTH_NOMINAL
        assert len(eh.sources) == 1
        source = eh.sources[0]
        assert source.name == "onboard_sensors"
        assert source.level == HealthLevel.HEALTH_NOMINAL
        assert len(source.subjects) == 1
        subject = source.subjects[0]
        assert subject.level == HealthLevel.HEALTH_NOMINAL
        check_names = {c.name for c in subject.checks}
        assert check_names == {"3d_gyro", "3d_accel", "3d_mag"}
        assert all(c.level == HealthLevel.HEALTH_NOMINAL for c in subject.checks)

    def test_degraded_when_enabled_bit_unhealthy(self):
        enabled = m.MAV_SYS_STATUS_SENSOR_3D_GYRO | m.MAV_SYS_STATUS_SENSOR_3D_ACCEL
        healthy = m.MAV_SYS_STATUS_SENSOR_3D_GYRO  # accel enabled but unhealthy
        out = dict(
            (s, env)
            for s, _, env in mk.map_sys_status(
                _build_sys_status(enabled_bits=enabled, healthy_bits=healthy), TS
            )
        )
        _, eh = _decode(out["entity_health"], EntityHealth)
        assert eh.level == HealthLevel.HEALTH_DEGRADED
        subject = eh.sources[0].subjects[0]
        assert subject.level == HealthLevel.HEALTH_DEGRADED
        by_name = {c.name: c for c in subject.checks}
        assert by_name["3d_gyro"].level == HealthLevel.HEALTH_NOMINAL
        assert by_name["3d_accel"].level == HealthLevel.HEALTH_DEGRADED

    def test_disabled_bits_are_skipped(self):
        # 3D_GYRO enabled and healthy; 3D_ACCEL not enabled (must not appear
        # as a check even though its healthy bit is also unset).
        out = dict(
            (s, env)
            for s, _, env in mk.map_sys_status(
                _build_sys_status(
                    enabled_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                    healthy_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                ),
                TS,
            )
        )
        _, eh = _decode(out["entity_health"], EntityHealth)
        check_names = {c.name for c in eh.sources[0].subjects[0].checks}
        assert check_names == {"3d_gyro"}

    def test_no_fence_enabled_subject_when_geofence_absent(self):
        # A vehicle with no geofence subsystem present must not emit
        # fence_enabled at all (consumers read its absence as "no fence").
        out = list(
            mk.map_sys_status(
                _build_sys_status(
                    enabled_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                    healthy_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                ),
                TS,
            )
        )
        assert "fence_enabled" not in [s for s, _, _ in out]

    def test_fence_enabled_true_when_geofence_enforced(self):
        # Geofence present AND enabled -> fence_enabled True.
        out = dict(
            (s, env)
            for s, _, env in mk.map_sys_status(
                _build_sys_status(
                    enabled_bits=m.MAV_SYS_STATUS_GEOFENCE,
                    healthy_bits=m.MAV_SYS_STATUS_GEOFENCE,
                ),
                TS,
            )
        )
        _, fence = _decode(out["fence_enabled"], TimestampedBool)
        assert fence.value is True

    def test_fence_enabled_false_when_present_but_disabled(self):
        # Geofence configured (present) but not currently enforcing (not in
        # the enabled bitmask) -> fence_enabled False, surfacing the real
        # autopilot state rather than the last enable_geofence RPC value.
        out = dict(
            (s, env)
            for s, _, env in mk.map_sys_status(
                _build_sys_status(
                    enabled_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                    healthy_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                    present_bits=m.MAV_SYS_STATUS_SENSOR_3D_GYRO
                    | m.MAV_SYS_STATUS_GEOFENCE,
                ),
                TS,
            )
        )
        _, fence = _decode(out["fence_enabled"], TimestampedBool)
        assert fence.value is False


# ---------------------------------------------------------------------------
# GLOBAL_POSITION_INT
# ---------------------------------------------------------------------------


def _build_global_position(
    lat_e7=575780000, lon_e7=119500000, alt_mm=12345, hdg_cdeg=18000
):
    return m.MAVLink_global_position_int_message(
        time_boot_ms=1234,
        lat=lat_e7,
        lon=lon_e7,
        alt=alt_mm,
        relative_alt=alt_mm,
        vx=100,  # 1.00 m/s
        vy=-50,  # -0.50 m/s
        vz=20,  # 0.20 m/s
        hdg=hdg_cdeg,
    )


class TestGlobalPositionInt:
    def test_publishes_location_fix_altitude_heading_velocity(self):
        out = list(mk.map_global_position_int(_build_global_position(), TS))
        subjects = [s for s, _, _ in out]
        assert "location_fix" in subjects
        assert "altitude_above_msl_m" in subjects
        assert "heading_true_north_deg" in subjects
        assert "ned_velocity_mps" in subjects
        # No source_id suffix on any of these
        assert all(suffix == "" for _, suffix, _ in out)

    def test_location_fix_decodes_lat_lon_alt(self):
        out = dict(
            (s, env)
            for s, _, env in mk.map_global_position_int(_build_global_position(), TS)
        )
        _, fix = _decode(out["location_fix"], LocationFix)
        assert fix.latitude == pytest.approx(57.578, rel=1e-6)
        assert fix.longitude == pytest.approx(11.95, rel=1e-6)
        assert fix.altitude == pytest.approx(12.345, rel=1e-6)
        assert fix.frame_id == "wgs84"

    def test_heading_skipped_when_unknown(self):
        # hdg = 65535 means unknown.
        out = list(
            mk.map_global_position_int(_build_global_position(hdg_cdeg=65535), TS)
        )
        subjects = [s for s, _, _ in out]
        assert "heading_true_north_deg" not in subjects

    def test_heading_decoded_when_known(self):
        # 18000 cdeg = 180.0 deg
        out = dict(
            (s, env)
            for s, _, env in mk.map_global_position_int(
                _build_global_position(hdg_cdeg=18000), TS
            )
        )
        _, hdg = _decode(out["heading_true_north_deg"], TimestampedFloat)
        assert hdg.value == pytest.approx(180.0)

    def test_ned_velocity_units_and_frame(self):
        out = dict(
            (s, env)
            for s, _, env in mk.map_global_position_int(_build_global_position(), TS)
        )
        _, vel = _decode(out["ned_velocity_mps"], Decomposed3DVector)
        assert vel.frame_id == "ned"
        assert vel.vector.x == pytest.approx(1.0)
        assert vel.vector.y == pytest.approx(-0.5)
        assert vel.vector.z == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# VFR_HUD
# ---------------------------------------------------------------------------


class TestVfrHud:
    def test_speed_throttle_climb(self):
        msg = m.MAVLink_vfr_hud_message(
            airspeed=0.0,
            groundspeed=2.5,  # m/s
            heading=180,
            throttle=42,
            alt=12.0,
            climb=0.5,
        )
        out = dict((s, env) for s, _, env in mk.map_vfr_hud(msg, TS))
        _, sog = _decode(out["speed_over_ground_knots"], TimestampedFloat)
        assert sog.value == pytest.approx(2.5 * 1.94384, rel=1e-4)
        _, climb = _decode(out["climb_rate_mps"], TimestampedFloat)
        assert climb.value == pytest.approx(0.5)
        _, thr = _decode(out["autopilot_throttle_pct"], TimestampedFloat)
        assert thr.value == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# GPS_RAW_INT
# ---------------------------------------------------------------------------


class TestGpsRawInt:
    def _build(self, **overrides):
        defaults = dict(
            time_usec=0,
            fix_type=3,
            lat=575780000,
            lon=119500000,
            alt=12345,
            eph=80,  # cm -> 0.80 m hdop
            epv=120,  # cm -> 1.20 m vdop
            vel=100,
            cog=9000,  # 90.00 deg
            satellites_visible=12,
        )
        defaults.update(overrides)
        return m.MAVLink_gps_raw_int_message(**defaults)

    def test_location_fix_under_gps_raw_suffix(self):
        out = list(mk.map_gps_raw_int(self._build(), TS))
        # find location_fix
        suffixes = {(s, suffix) for s, suffix, _ in out}
        assert ("location_fix", "/gps_raw") in suffixes

    def test_location_fix_quality_published(self):
        # MAVLink fix_type 4 (DGPS) -> 3D fix, pseudorange-differential.
        out = list(mk.map_gps_raw_int(self._build(fix_type=4), TS))
        d = {s: env for s, _, env in out}
        _, q = _decode(d["location_fix_quality"], LocationFixQuality)
        assert q.fix_type == LocationFixQuality.FIX_3D
        assert q.pos_type == LocationFixQuality.POS_TYPE_PSRDIFF
        assert q.rtk_status == LocationFixQuality.RTK_STATUS_DIFFERENTIAL

    def test_location_fix_quality_rtk_fixed(self):
        # MAVLink fix_type 6 (RTK_FIXED) carries the RTK status through.
        out = list(mk.map_gps_raw_int(self._build(fix_type=6), TS))
        d = {s: env for s, _, env in out}
        _, q = _decode(d["location_fix_quality"], LocationFixQuality)
        assert q.fix_type == LocationFixQuality.FIX_3D
        assert q.pos_type == LocationFixQuality.POS_TYPE_RTK_INT
        assert q.rtk_status == LocationFixQuality.RTK_STATUS_FIXED

    def test_satellite_count(self):
        out = list(mk.map_gps_raw_int(self._build(satellites_visible=9), TS))
        d = {s: env for s, _, env in out}
        _, sats = _decode(d["location_fix_satellites_visible"], TimestampedInt)
        assert sats.value == 9

    def test_hdop_vdop_from_eph_epv(self):
        out = list(mk.map_gps_raw_int(self._build(eph=80, epv=120), TS))
        d = {s: env for s, _, env in out}
        _, hdop = _decode(d["location_fix_hdop"], TimestampedFloat)
        _, vdop = _decode(d["location_fix_vdop"], TimestampedFloat)
        assert hdop.value == pytest.approx(0.80)
        assert vdop.value == pytest.approx(1.20)

    def test_hdop_vdop_skipped_when_unknown(self):
        out = list(mk.map_gps_raw_int(self._build(eph=65535, epv=65535), TS))
        subjects = [s for s, _, _ in out]
        assert "location_fix_hdop" not in subjects
        assert "location_fix_vdop" not in subjects

    def test_cog_skipped_when_unknown(self):
        out = list(mk.map_gps_raw_int(self._build(cog=65535), TS))
        subjects = [s for s, _, _ in out]
        assert "course_over_ground_deg" not in subjects


# ---------------------------------------------------------------------------
# ATTITUDE
# ---------------------------------------------------------------------------


class TestAttitude:
    def test_radians_converted_to_degrees(self):
        msg = m.MAVLink_attitude_message(
            time_boot_ms=0,
            roll=math.pi / 4,  # 45 deg
            pitch=-math.pi / 6,  # -30 deg
            yaw=math.pi,  # 180 deg
            rollspeed=math.pi / 2,  # 90 deg/s
            pitchspeed=0.0,
            yawspeed=-math.pi / 4,  # -45 deg/s
        )
        d = {s: env for s, _, env in mk.map_attitude(msg, TS)}
        _, roll = _decode(d["roll_deg"], TimestampedFloat)
        _, pitch = _decode(d["pitch_deg"], TimestampedFloat)
        _, yaw = _decode(d["yaw_deg"], TimestampedFloat)
        _, rs = _decode(d["roll_rate_degps"], TimestampedFloat)
        _, ys = _decode(d["yaw_rate_degps"], TimestampedFloat)
        assert roll.value == pytest.approx(45.0)
        assert pitch.value == pytest.approx(-30.0)
        assert yaw.value == pytest.approx(180.0)
        assert rs.value == pytest.approx(90.0)
        assert ys.value == pytest.approx(-45.0)


# ---------------------------------------------------------------------------
# ATTITUDE_QUATERNION
# ---------------------------------------------------------------------------


class TestAttitudeQuaternion:
    def test_w_x_y_z_order(self):
        msg = m.MAVLink_attitude_quaternion_message(
            time_boot_ms=0,
            q1=1.0,  # w
            q2=0.0,  # x
            q3=0.0,  # y
            q4=0.0,  # z
            rollspeed=0.0,
            pitchspeed=0.0,
            yawspeed=0.0,
        )
        d = {s: env for s, _, env in mk.map_attitude_quaternion(msg, TS)}
        _, q = _decode(d["orientation_quaternion"], TimestampedQuaternion)
        assert q.value.w == pytest.approx(1.0)
        assert q.value.x == pytest.approx(0.0)
        assert q.value.y == pytest.approx(0.0)
        assert q.value.z == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# BATTERY_STATUS
# ---------------------------------------------------------------------------


class TestBatteryStatus:
    def _build(self, **overrides):
        defaults = dict(
            id=0,
            battery_function=0,
            type=0,
            temperature=2500,  # 25.00 C
            voltages=[16800, -1, -1, -1, -1, -1, -1, -1, -1, -1],  # 16.8 V cell0
            current_battery=350,  # 3.50 A
            current_consumed=-1,
            energy_consumed=-1,
            battery_remaining=78,
        )
        defaults.update(overrides)
        return m.MAVLink_battery_status_message(**defaults)

    def test_voltage_current_charge_temp(self):
        d = {s: env for s, _, env in mk.map_battery_status(self._build(), TS)}
        _, v = _decode(d["battery_voltage_v"], TimestampedFloat)
        _, i = _decode(d["battery_current_a"], TimestampedFloat)
        _, soc = _decode(d["battery_state_of_charge_pct"], TimestampedFloat)
        _, t = _decode(d["battery_temperature_celsius"], TimestampedFloat)
        assert v.value == pytest.approx(16.8)
        assert i.value == pytest.approx(3.5)
        assert soc.value == pytest.approx(78.0)
        assert t.value == pytest.approx(25.0)

    def test_unknown_fields_skipped(self):
        msg = self._build(
            voltages=[-1] * 10,
            current_battery=-1,
            battery_remaining=-1,
            temperature=32767,
        )
        out = list(mk.map_battery_status(msg, TS))
        assert out == []


# ---------------------------------------------------------------------------
# POSITION_TARGET_GLOBAL_INT
# ---------------------------------------------------------------------------


class TestPositionTargetGlobalInt:
    def _build(self, lat_e7=575780000, lon_e7=119500000, alt_m=5.0):
        return m.MAVLink_position_target_global_int_message(
            time_boot_ms=1234,
            coordinate_frame=m.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            type_mask=0,
            lat_int=lat_e7,
            lon_int=lon_e7,
            alt=alt_m,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            afx=0.0,
            afy=0.0,
            afz=0.0,
            yaw=0.0,
            yaw_rate=0.0,
        )

    def test_emits_navigation_target_echo(self):
        out = list(mk.map_position_target_global_int(self._build(), TS))
        assert [s for s, _, _ in out] == ["navigation_target_echo"]
        assert all(suffix == "" for _, suffix, _ in out)

    def test_decodes_lat_lon_alt(self):
        out = dict(
            (s, env)
            for s, _, env in mk.map_position_target_global_int(self._build(), TS)
        )
        _, fix = _decode(out["navigation_target_echo"], LocationFix)
        assert fix.latitude == pytest.approx(57.578, rel=1e-6)
        assert fix.longitude == pytest.approx(11.95, rel=1e-6)
        assert fix.altitude == pytest.approx(5.0, rel=1e-6)
        assert fix.frame_id == "wgs84"

    def test_skips_zero_zero_target(self):
        # (0, 0) means the position fields are being ignored — no active
        # target, so nothing is published.
        out = list(
            mk.map_position_target_global_int(self._build(lat_e7=0, lon_e7=0), TS)
        )
        assert out == []


# ---------------------------------------------------------------------------
# MISSION_CURRENT
# ---------------------------------------------------------------------------


class TestMissionCurrent:
    def test_emits_mission_current_seq(self):
        msg = m.MAVLink_mission_current_message(
            seq=7, total=12, mission_state=0, mission_mode=0
        )
        out = list(mk.map_mission_current(msg, TS))
        assert [s for s, _, _ in out] == ["mission_current_seq"]
        d = {s: env for s, _, env in out}
        _, seq = _decode(d["mission_current_seq"], TimestampedInt)
        assert seq.value == 7


# ---------------------------------------------------------------------------
# Dispatch table coverage
# ---------------------------------------------------------------------------


class TestDispatchTable:
    def test_unmapped_messages_dropped_silently(self):
        # Build a message we don't handle.
        msg = m.MAVLink_statustext_message(severity=4, text=b"info", id=0, chunk_seq=0)

        # Use the dispatch with a fake session that records puts.
        class _FakeSession:
            def declare_publisher(self, key):
                return _FakePub()

        class _FakePub:
            def __init__(self):
                self.calls = []

            def put(self, env):
                self.calls.append(env)

        published = mk.dispatch(msg, _FakeSession(), "r", "e", "s")
        assert published == 0

    def test_handler_keys_match_real_mavlink_message_names(self):
        # Each key in the dispatch table must be a real MAVLink message name.
        dialect_names = {
            cls.name for cls in m.mavlink_map.values() if hasattr(cls, "name")
        }
        for key in mk.MESSAGE_HANDLERS:
            assert key in dialect_names, f"Unknown MAVLink message name: {key}"
