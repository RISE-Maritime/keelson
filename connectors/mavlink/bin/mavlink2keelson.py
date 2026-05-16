#!/usr/bin/env python3

"""
Direct MAVLink -> Keelson uplink.

Reads MAVLink messages from a UDP/serial/TLog endpoint via pymavlink and
republishes them as typed keelson Envelopes on the well-known subjects used
today by ``keelson-connector-blueos``.

Designed as a drop-in replacement for the
``BlueOS -> blueos-gateway -> keelson-connector-blueos`` chain on the
telemetry path.

Subject contract is intentionally identical to ``keelson-connector-blueos``
so existing consumers (MCAP recording, Foxglove, autonomy stacks) keep
working unchanged.
"""

import argparse
import logging
import math
import queue
import time
from typing import Any, Callable, Iterable, Optional, Tuple

import zenoh

# pymavlink dialect — ArduPilot superset of common.
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink_dialect

import keelson
from keelson import enclose
from keelson.helpers import (
    enclose_from_float,
    enclose_from_integer,
    enclose_from_string,
)
from keelson.payloads.Decomposed3DVector_pb2 import Decomposed3DVector
from keelson.payloads.ManualControl_pb2 import ManualControl
from keelson.payloads.EntityHealth_pb2 import (
    CheckResult,
    EntityHealth,
    HealthLevel,
    SourceHealth,
)
from keelson.payloads.Primitives_pb2 import (
    TimestampedBool,
    TimestampedQuaternion,
    TimestampedString,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
)


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

PUBLISHERS: dict[Tuple[str, str], "zenoh.Publisher"] = {}

logger = logging.getLogger("mavlink2keelson")


# ---------------------------------------------------------------------------
# Small enclose-helpers not in keelson.helpers
# ---------------------------------------------------------------------------


def enclose_from_bool(value: bool, timestamp: Optional[int] = None) -> bytes:
    payload = TimestampedBool()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value
    return enclose(payload.SerializeToString())


def enclose_from_quaternion(
    w: float, x: float, y: float, z: float, timestamp: Optional[int] = None
) -> bytes:
    payload = TimestampedQuaternion()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value.w = w
    payload.value.x = x
    payload.value.y = y
    payload.value.z = z
    return enclose(payload.SerializeToString())


def enclose_from_decomposed_vector(
    x: float,
    y: float,
    z: float,
    frame_id: str,
    timestamp: Optional[int] = None,
) -> bytes:
    payload = Decomposed3DVector()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.frame_id = frame_id
    payload.vector.x = x
    payload.vector.y = y
    payload.vector.z = z
    return enclose(payload.SerializeToString())


def enclose_from_location_fix(
    latitude: float,
    longitude: float,
    altitude: Optional[float] = None,
    h_acc_m: Optional[float] = None,
    v_acc_m: Optional[float] = None,
    frame_id: str = "wgs84",
    timestamp: Optional[int] = None,
) -> bytes:
    payload = LocationFix()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.frame_id = frame_id
    payload.latitude = latitude
    payload.longitude = longitude
    if altitude is not None:
        payload.altitude = altitude
    if h_acc_m is not None and v_acc_m is not None:
        # 3x3 row-major ENU covariance, diagonal only.
        cov_e = h_acc_m * h_acc_m
        cov_n = h_acc_m * h_acc_m
        cov_u = v_acc_m * v_acc_m
        payload.position_covariance[:] = [
            cov_e,
            0.0,
            0.0,
            0.0,
            cov_n,
            0.0,
            0.0,
            0.0,
            cov_u,
        ]
        payload.position_covariance_type = LocationFix.APPROXIMATED
    return enclose(payload.SerializeToString())


# ---------------------------------------------------------------------------
# EntityHealth from HEARTBEAT / SYS_STATUS
# ---------------------------------------------------------------------------


def _health_level_from_mav_state(mav_state: int) -> "HealthLevel.V":
    """Translate a MAV_STATE value to a keelson HealthLevel."""
    mapping = {
        mavlink_dialect.MAV_STATE_UNINIT: HealthLevel.HEALTH_UNKNOWN,
        mavlink_dialect.MAV_STATE_BOOT: HealthLevel.HEALTH_INACTIVE,
        mavlink_dialect.MAV_STATE_CALIBRATING: HealthLevel.HEALTH_DEGRADED,
        mavlink_dialect.MAV_STATE_STANDBY: HealthLevel.HEALTH_NOMINAL,
        mavlink_dialect.MAV_STATE_ACTIVE: HealthLevel.HEALTH_NOMINAL,
        mavlink_dialect.MAV_STATE_CRITICAL: HealthLevel.HEALTH_CRITICAL,
        mavlink_dialect.MAV_STATE_EMERGENCY: HealthLevel.HEALTH_CRITICAL,
        mavlink_dialect.MAV_STATE_POWEROFF: HealthLevel.HEALTH_INACTIVE,
        mavlink_dialect.MAV_STATE_FLIGHT_TERMINATION: HealthLevel.HEALTH_CRITICAL,
    }
    return mapping.get(mav_state, HealthLevel.HEALTH_UNKNOWN)


def build_entity_health_from_heartbeat(msg, timestamp_ns: int) -> bytes:
    """Build a minimal EntityHealth envelope from a HEARTBEAT message."""
    payload = EntityHealth()
    payload.timestamp.FromNanoseconds(timestamp_ns)
    payload.level = _health_level_from_mav_state(msg.system_status)
    payload.rate_hz = 1.0  # HEARTBEAT is canonically 1 Hz from ArduPilot.
    return enclose(payload.SerializeToString())


def build_entity_health_from_sys_status(msg, timestamp_ns: int) -> bytes:
    """Build EntityHealth from SYS_STATUS, with per-sensor CheckResults."""
    payload = EntityHealth()
    payload.timestamp.FromNanoseconds(timestamp_ns)

    enabled = msg.onboard_control_sensors_enabled
    health = msg.onboard_control_sensors_health

    sensors = SourceHealth()
    sensors.name = "onboard_sensors"
    overall = HealthLevel.HEALTH_NOMINAL

    # Walk the named bits in MAV_SYS_STATUS_SENSOR.
    for attr in dir(mavlink_dialect):
        if not attr.startswith("MAV_SYS_STATUS_SENSOR_"):
            continue
        bit = getattr(mavlink_dialect, attr)
        if not isinstance(bit, int) or bit == 0:
            continue
        if not (enabled & bit):
            continue
        check = CheckResult()
        check.name = attr.removeprefix("MAV_SYS_STATUS_SENSOR_").lower()
        if health & bit:
            check.level = HealthLevel.HEALTH_NOMINAL
            check.detail = "ok"
        else:
            check.level = HealthLevel.HEALTH_DEGRADED
            check.detail = "sensor reports unhealthy"
            overall = HealthLevel.HEALTH_DEGRADED
        sensors.checks.append(check)

    sensors.level = overall
    payload.sources.append(sensors)
    payload.level = overall
    return enclose(payload.SerializeToString())


# ---------------------------------------------------------------------------
# MAVLink message -> (subject, source_id_suffix, envelope_bytes) mappers
#
# Each mapper is a pure function: takes a parsed mavlink message and a
# timestamp, returns an iterable of (subject, source_id_suffix, envelope_bytes).
#
# The source_id_suffix is appended to the connector's --source-id; "" means
# "no suffix" (publish under the base source-id).
# ---------------------------------------------------------------------------


Mapping = Iterable[Tuple[str, str, bytes]]


def map_heartbeat(msg, ts: int) -> Mapping:
    mode_name = mavutil.mode_string_v10(msg)
    armed = bool(msg.base_mode & mavlink_dialect.MAV_MODE_FLAG_SAFETY_ARMED)
    yield "vehicle_mode", "", enclose_from_string(mode_name, timestamp=ts)
    yield "vehicle_armed", "", enclose_from_bool(armed, timestamp=ts)
    yield "entity_health", "", build_entity_health_from_heartbeat(msg, ts)


def map_sys_status(msg, ts: int) -> Mapping:
    yield "entity_health", "", build_entity_health_from_sys_status(msg, ts)
    # SYS_STATUS also reports battery — but BATTERY_STATUS is more complete,
    # so we let that handler own those subjects.


def map_global_position_int(msg, ts: int) -> Mapping:
    lat = msg.lat / 1e7
    lon = msg.lon / 1e7
    alt_msl_m = msg.alt / 1000.0
    yield "location_fix", "", enclose_from_location_fix(
        latitude=lat, longitude=lon, altitude=alt_msl_m, timestamp=ts
    )
    yield "altitude_above_msl_m", "", enclose_from_float(alt_msl_m, timestamp=ts)
    if msg.hdg != 65535:  # UINT16_MAX = unknown
        yield "heading_true_north_deg", "", enclose_from_float(
            msg.hdg / 100.0, timestamp=ts
        )
    yield "ned_velocity_mps", "", enclose_from_decomposed_vector(
        x=msg.vx / 100.0,
        y=msg.vy / 100.0,
        z=msg.vz / 100.0,
        frame_id="ned",
        timestamp=ts,
    )


def map_vfr_hud(msg, ts: int) -> Mapping:
    yield "speed_over_ground_knots", "", enclose_from_float(
        msg.groundspeed * 1.94384, timestamp=ts
    )
    yield "climb_rate_mps", "", enclose_from_float(msg.climb, timestamp=ts)
    yield "autopilot_throttle_pct", "", enclose_from_float(
        float(msg.throttle), timestamp=ts
    )


def map_gps_raw_int(msg, ts: int) -> Mapping:
    lat = msg.lat / 1e7
    lon = msg.lon / 1e7
    alt_m = msg.alt / 1000.0
    h_acc = msg.eph / 100.0 if msg.eph != 65535 else None
    v_acc = msg.epv / 100.0 if msg.epv != 65535 else None
    yield "location_fix", "/gps_raw", enclose_from_location_fix(
        latitude=lat,
        longitude=lon,
        altitude=alt_m,
        h_acc_m=h_acc,
        v_acc_m=v_acc,
        timestamp=ts,
    )
    yield "gps_fix_type", "", enclose_from_integer(int(msg.fix_type), timestamp=ts)
    yield "location_fix_satellites_visible", "", enclose_from_integer(
        int(msg.satellites_visible), timestamp=ts
    )
    if h_acc is not None:
        yield "location_fix_hdop", "", enclose_from_float(h_acc, timestamp=ts)
    if v_acc is not None:
        yield "location_fix_vdop", "", enclose_from_float(v_acc, timestamp=ts)
    if msg.cog != 65535:
        yield "course_over_ground_deg", "", enclose_from_float(
            msg.cog / 100.0, timestamp=ts
        )


def map_attitude(msg, ts: int) -> Mapping:
    yield "roll_deg", "", enclose_from_float(math.degrees(msg.roll), timestamp=ts)
    yield "pitch_deg", "", enclose_from_float(math.degrees(msg.pitch), timestamp=ts)
    yield "yaw_deg", "", enclose_from_float(math.degrees(msg.yaw), timestamp=ts)
    yield "roll_rate_degps", "", enclose_from_float(
        math.degrees(msg.rollspeed), timestamp=ts
    )
    yield "pitch_rate_degps", "", enclose_from_float(
        math.degrees(msg.pitchspeed), timestamp=ts
    )
    yield "yaw_rate_degps", "", enclose_from_float(
        math.degrees(msg.yawspeed), timestamp=ts
    )


def map_attitude_quaternion(msg, ts: int) -> Mapping:
    # MAVLink: q1=w, q2=x, q3=y, q4=z
    yield "orientation_quaternion", "", enclose_from_quaternion(
        w=msg.q1, x=msg.q2, y=msg.q3, z=msg.q4, timestamp=ts
    )


def map_local_position_ned(msg, ts: int) -> Mapping:
    # MAVLink LOCAL_POSITION_NED is in NED; map to body-frame surge/sway/heave
    # under the documented assumption that origin frame is aligned with vehicle
    # heading. Operators with a different frame configuration should map this
    # downstream.
    yield "surge_m", "", enclose_from_float(msg.x, timestamp=ts)
    yield "sway_m", "", enclose_from_float(msg.y, timestamp=ts)
    yield "heave_m", "", enclose_from_float(msg.z, timestamp=ts)


def map_raw_imu(msg, ts: int) -> Mapping:
    # RAW_IMU units: accel in mg, gyro in mrad/s, mag in mGauss.
    yield "linear_acceleration_mpss", "", enclose_from_decomposed_vector(
        x=msg.xacc * 9.80665e-3,
        y=msg.yacc * 9.80665e-3,
        z=msg.zacc * 9.80665e-3,
        frame_id="body",
        timestamp=ts,
    )
    yield "angular_velocity_radps", "", enclose_from_decomposed_vector(
        x=msg.xgyro * 1e-3,
        y=msg.ygyro * 1e-3,
        z=msg.zgyro * 1e-3,
        frame_id="body",
        timestamp=ts,
    )
    yield "magnetic_field_gauss", "", enclose_from_decomposed_vector(
        x=msg.xmag * 1e-3,
        y=msg.ymag * 1e-3,
        z=msg.zmag * 1e-3,
        frame_id="body",
        timestamp=ts,
    )


def map_scaled_imu(msg, ts: int) -> Mapping:
    # SCALED_IMU units: accel in mg, gyro in mrad/s, mag in mGauss.
    yield "linear_acceleration_mpss", "", enclose_from_decomposed_vector(
        x=msg.xacc * 9.80665e-3,
        y=msg.yacc * 9.80665e-3,
        z=msg.zacc * 9.80665e-3,
        frame_id="body",
        timestamp=ts,
    )
    yield "angular_velocity_radps", "", enclose_from_decomposed_vector(
        x=msg.xgyro * 1e-3,
        y=msg.ygyro * 1e-3,
        z=msg.zgyro * 1e-3,
        frame_id="body",
        timestamp=ts,
    )
    yield "magnetic_field_gauss", "", enclose_from_decomposed_vector(
        x=msg.xmag * 1e-3,
        y=msg.ymag * 1e-3,
        z=msg.zmag * 1e-3,
        frame_id="body",
        timestamp=ts,
    )


def map_battery_status(msg, ts: int) -> Mapping:
    # voltages[0] is millivolts; -1 means "unknown". Skip if unknown.
    if msg.voltages and msg.voltages[0] != -1:
        yield "battery_voltage_v", "", enclose_from_float(
            msg.voltages[0] / 1000.0, timestamp=ts
        )
    if msg.current_battery != -1:
        # current_battery is in 10*mA units (cA).
        yield "battery_current_a", "", enclose_from_float(
            msg.current_battery / 100.0, timestamp=ts
        )
    if msg.battery_remaining != -1:
        yield "battery_state_of_charge_pct", "", enclose_from_float(
            float(msg.battery_remaining), timestamp=ts
        )
    if msg.temperature != 32767:  # INT16_MAX = unknown
        yield "battery_temperature_celsius", "", enclose_from_float(
            msg.temperature / 100.0, timestamp=ts
        )


# Dispatch table. Keyed by MAVLink message-name string (msg.get_type()).
MESSAGE_HANDLERS: dict[str, Callable[..., Mapping]] = {
    "HEARTBEAT": map_heartbeat,
    "SYS_STATUS": map_sys_status,
    "GLOBAL_POSITION_INT": map_global_position_int,
    "VFR_HUD": map_vfr_hud,
    "GPS_RAW_INT": map_gps_raw_int,
    "ATTITUDE": map_attitude,
    "ATTITUDE_QUATERNION": map_attitude_quaternion,
    "LOCAL_POSITION_NED": map_local_position_ned,
    "RAW_IMU": map_raw_imu,
    "SCALED_IMU": map_scaled_imu,
    "SCALED_IMU2": map_scaled_imu,
    "SCALED_IMU3": map_scaled_imu,
    "BATTERY_STATUS": map_battery_status,
}


# ---------------------------------------------------------------------------
# Publisher cache + dispatch
# ---------------------------------------------------------------------------


def _get_or_create_publisher(
    session: zenoh.Session,
    realm: str,
    entity_id: str,
    subject: str,
    source_id: str,
) -> "zenoh.Publisher":
    cache_key = (subject, source_id)
    pub = PUBLISHERS.get(cache_key)
    if pub is None:
        key_expr = keelson.construct_pubsub_key(realm, entity_id, subject, source_id)
        pub = session.declare_publisher(key_expr)
        PUBLISHERS[cache_key] = pub
        logger.info("Declared publisher: %s", key_expr)
    return pub


def dispatch(
    msg,
    session: zenoh.Session,
    realm: str,
    entity_id: str,
    base_source_id: str,
) -> int:
    """Run the appropriate mapper for ``msg`` and publish each output. Returns
    the number of envelopes published."""
    handler = MESSAGE_HANDLERS.get(msg.get_type())
    if handler is None:
        logger.debug("Unmapped MAVLink message: %s", msg.get_type())
        return 0

    ts = time.time_ns()
    published = 0
    try:
        for subject, suffix, envelope_bytes in handler(msg, ts):
            source_id = f"{base_source_id}{suffix}" if suffix else base_source_id
            pub = _get_or_create_publisher(
                session, realm, entity_id, subject, source_id
            )
            pub.put(envelope_bytes)
            published += 1
    except Exception:  # noqa: BLE001 — never let a single bad msg kill the loop
        logger.exception("Failed to dispatch %s", msg.get_type())
    return published


# ---------------------------------------------------------------------------
# CLI + main
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mavlink2keelson",
        description=(
            "Direct MAVLink (ArduPilot/PX4) -> Keelson uplink. "
            "Supersedes the keelson-connector-blueos + blueos-gateway chain "
            "for the telemetry path."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", required=True, help="Keelson realm")
    parser.add_argument("-e", "--entity-id", required=True, help="Entity (vehicle) ID")
    parser.add_argument(
        "-s",
        "--source-id",
        required=True,
        help="Base source ID (some subjects are suffixed, e.g. '/gps_raw')",
    )

    parser.add_argument(
        "--mavlink-url",
        required=True,
        help=(
            "pymavlink connection string, e.g. 'udpin:0.0.0.0:14550', "
            "'/dev/ttyUSB0', 'tlog:flight.tlog'"
        ),
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=57600,
        help="Baud rate (only used for serial --mavlink-url)",
    )
    parser.add_argument(
        "--source-system",
        type=int,
        default=254,
        help="MAVLink source system ID for our outgoing messages (use a value "
        "different from any other GCS to avoid heartbeat collisions)",
    )
    parser.add_argument(
        "--source-component",
        type=int,
        default=mavlink_dialect.MAV_COMP_ID_ONBOARD_COMPUTER,
        help="MAVLink source component ID for our outgoing messages",
    )
    parser.add_argument(
        "--target-system",
        type=int,
        required=True,
        help="MAVLink target system ID to filter on (the vehicle)",
    )
    parser.add_argument(
        "--target-component",
        type=int,
        default=0,
        help="MAVLink target component ID to filter on (0 = any component)",
    )
    parser.add_argument(
        "--recv-timeout",
        type=float,
        default=1.0,
        help="Per-recv timeout in seconds (controls shutdown responsiveness)",
    )
    return parser


# ---------------------------------------------------------------------------
# Downlink: Zenoh manual_control -> MAVLink MANUAL_CONTROL
# ---------------------------------------------------------------------------


def _send_manual_control(
    mav, target_system: int, mc: ManualControl
) -> None:
    """Translate a Keelson ManualControl payload into MAVLink RC_CHANNELS_OVERRIDE.

    Proto axes are unitless [-1.0, 1.0]; we map them to RC channel PWM values
    centered at 1500us with ±500us swing:

        steering -> channel 1 (ArduPilot default RC_MAP_STEERING)
        throttle -> channel 3 (ArduPilot default RC_MAP_THROTTLE)

    Channel values of 0 (= release) are sent for other channels so we don't
    inadvertently override e.g. the mode switch on RC5/8.

    NOTE: ArduPilot drops RC overrides whose sender sysid != ``SYSID_MYGCS``
    (default 255). If ``--source-system`` is set away from the default 254,
    ``SYSID_MYGCS`` on the autopilot must match.
    """
    def _to_pwm(v: float) -> int:
        return int(round(1500 + max(-1.0, min(1.0, v)) * 500))

    mav.mav.rc_channels_override_send(
        target_system,
        0,                       # target component (0 = any)
        _to_pwm(mc.steering),    # ch1 = steering
        0,                       # ch2 release
        _to_pwm(mc.throttle),    # ch3 = throttle
        0, 0, 0, 0, 0,           # ch4..ch8 release
    )


def _setup_manual_control_subscriber(
    session: zenoh.Session,
    args: argparse.Namespace,
    cmd_queue: "queue.Queue[ManualControl]",
) -> "zenoh.Subscriber":
    """Subscribe to manual_control under our entity and queue each command for
    the main loop to forward to MAVLink. We accept commands from any source-id
    so external GCSes/joysticks can drive the vehicle."""
    key = keelson.construct_pubsub_key(
        args.realm, args.entity_id, "manual_control", "**"
    )

    def _on_sample(sample: "zenoh.Sample") -> None:
        try:
            _, _, payload_bytes = keelson.uncover(bytes(sample.payload.to_bytes()))
            mc = ManualControl()
            mc.ParseFromString(payload_bytes)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decode manual_control envelope")
            return
        try:
            cmd_queue.put_nowait(mc)
        except queue.Full:
            logger.warning("manual_control queue full; dropping command")
        else:
            logger.debug(
                "Queued ManualControl: steering=%.2f throttle=%.2f (depth=%d)",
                mc.steering, mc.throttle, cmd_queue.qsize(),
            )

    logger.info("Subscribing to %s for manual_control", key)
    return session.declare_subscriber(key, _on_sample)


def _drain_command_queue(
    mav, target_system: int, cmd_queue: "queue.Queue[ManualControl]"
) -> int:
    """Pull every queued ManualControl and forward to MAVLink. Returns count sent."""
    sent = 0
    while True:
        try:
            mc = cmd_queue.get_nowait()
        except queue.Empty:
            if sent:
                logger.debug(
                    "Forwarded %d ManualControl frames as RC overrides to target %d",
                    sent, target_system,
                )
            return sent
        try:
            _send_manual_control(mav, target_system, mc)
            sent += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send RC override")


# ---------------------------------------------------------------------------
# Downlink: cmd_arm + cmd_set_mode
# ---------------------------------------------------------------------------


def _send_arm_disarm(mav, target_system: int, target_component: int, arm: bool) -> None:
    """Send a MAV_CMD_COMPONENT_ARM_DISARM via COMMAND_LONG. param2 stays 0
    (no force) — arming-check bypass should be configured on the autopilot
    rather than forced via a kill-switch from the GCS."""
    mav.mav.command_long_send(
        target_system,
        target_component if target_component != 0 else 1,
        mavlink_dialect.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1.0 if arm else 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    )


def _send_set_mode(mav, target_system: int, mode_name: str) -> bool:
    """Look the mode name up in pymavlink's vehicle-aware mapping (populated
    from received HEARTBEATs) and send a SET_MODE. Returns True on send."""
    mode_map = mav.mode_mapping() or {}
    mode_id = mode_map.get(mode_name.upper())
    if mode_id is None:
        logger.error(
            "Unknown mode %r; known modes for this vehicle: %s",
            mode_name, sorted(mode_map.keys()),
        )
        return False
    mav.mav.set_mode_send(
        target_system,
        mavlink_dialect.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mode_id,
    )
    return True


def _setup_arm_subscriber(
    session: zenoh.Session,
    args: argparse.Namespace,
    arm_queue: "queue.Queue[bool]",
) -> "zenoh.Subscriber":
    key = keelson.construct_pubsub_key(args.realm, args.entity_id, "cmd_arm", "**")

    def _on_sample(sample: "zenoh.Sample") -> None:
        try:
            _, _, payload_bytes = keelson.uncover(bytes(sample.payload.to_bytes()))
            msg = TimestampedBool()
            msg.ParseFromString(payload_bytes)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decode cmd_arm envelope")
            return
        try:
            arm_queue.put_nowait(bool(msg.value))
            logger.info("Queued cmd_arm: %s", msg.value)
        except queue.Full:
            logger.warning("cmd_arm queue full; dropping command")

    logger.info("Subscribing to %s for cmd_arm", key)
    return session.declare_subscriber(key, _on_sample)


def _setup_set_mode_subscriber(
    session: zenoh.Session,
    args: argparse.Namespace,
    mode_queue: "queue.Queue[str]",
) -> "zenoh.Subscriber":
    key = keelson.construct_pubsub_key(args.realm, args.entity_id, "cmd_set_mode", "**")

    def _on_sample(sample: "zenoh.Sample") -> None:
        try:
            _, _, payload_bytes = keelson.uncover(bytes(sample.payload.to_bytes()))
            msg = TimestampedString()
            msg.ParseFromString(payload_bytes)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decode cmd_set_mode envelope")
            return
        try:
            mode_queue.put_nowait(msg.value)
            logger.info("Queued cmd_set_mode: %s", msg.value)
        except queue.Full:
            logger.warning("cmd_set_mode queue full; dropping command")

    logger.info("Subscribing to %s for cmd_set_mode", key)
    return session.declare_subscriber(key, _on_sample)


def _drain_arm_queue(
    mav, target_system: int, target_component: int, arm_queue: "queue.Queue[bool]"
) -> int:
    sent = 0
    while True:
        try:
            arm = arm_queue.get_nowait()
        except queue.Empty:
            return sent
        try:
            _send_arm_disarm(mav, target_system, target_component, arm)
            sent += 1
            logger.info("Sent ARM_DISARM (%s) to target %d", arm, target_system)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send ARM_DISARM")


def _drain_mode_queue(
    mav, target_system: int, mode_queue: "queue.Queue[str]"
) -> int:
    sent = 0
    while True:
        try:
            mode_name = mode_queue.get_nowait()
        except queue.Empty:
            return sent
        try:
            if _send_set_mode(mav, target_system, mode_name):
                sent += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send SET_MODE")


def open_mavlink(url: str, source_system: int, source_component: int, baud: int):
    """Wrap ``mavutil.mavlink_connection`` so the caller doesn't have to know
    pymavlink's URL conventions.

    Recognized URL forms:
        ``udpin:HOST:PORT`` / ``udpout:HOST:PORT`` / ``tcp:HOST:PORT`` —
            passed through to pymavlink unchanged.
        ``tlog:PATH`` — convenience prefix; ``tlog:`` is stripped and the
            file path is passed to pymavlink which auto-detects the format.
        ``PATH`` (no scheme) — treated as a serial port; ``baud`` is passed.
    """
    logger.info("Opening MAVLink connection: %s", url)
    kwargs = {
        "source_system": source_system,
        "source_component": source_component,
    }
    if url.startswith("tlog:"):
        url = url.removeprefix("tlog:")
    elif not (
        url.startswith("udp") or url.startswith("tcp") or url.startswith("mcap:")
    ):
        # Bare path → serial port.
        kwargs["baud"] = baud
    return mavutil.mavlink_connection(url, **kwargs)


def run(args: argparse.Namespace) -> int:
    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)

    mav = open_mavlink(
        args.mavlink_url, args.source_system, args.source_component, args.baud
    )

    cmd_queue: "queue.Queue[ManualControl]" = queue.Queue(maxsize=1024)
    arm_queue: "queue.Queue[bool]" = queue.Queue(maxsize=64)
    mode_queue: "queue.Queue[str]" = queue.Queue(maxsize=64)

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session, GracefulShutdown() as shutdown:
        manual_control_sub = _setup_manual_control_subscriber(
            session, args, cmd_queue
        )
        arm_sub = _setup_arm_subscriber(session, args, arm_queue)
        set_mode_sub = _setup_set_mode_subscriber(session, args, mode_queue)

        liveliness_ctx: Optional[Any] = None
        liveliness_token = None
        message_count = 0

        try:
            while not shutdown.is_requested():
                # Block briefly so we can check for shutdown periodically.
                msg = mav.recv_match(blocking=True, timeout=args.recv_timeout)

                if msg is not None and msg.get_type() != "BAD_DATA":
                    # target_system filtering. system_id 0 in MAVLink means
                    # broadcast, so accept it too.
                    src_sys = msg.get_srcSystem()
                    if src_sys in (0, args.target_system) and (
                        args.target_component == 0
                        or msg.get_srcComponent()
                        in (0, args.target_component)
                    ):
                        # Declare liveliness on first valid HEARTBEAT.
                        if liveliness_token is None and msg.get_type() == "HEARTBEAT":
                            liveliness_ctx = declare_liveliness_token(
                                session, args.realm, args.entity_id, args.source_id
                            )
                            liveliness_token = liveliness_ctx.__enter__()
                            logger.info(
                                "Declared liveliness token after first HEARTBEAT"
                            )

                        published = dispatch(
                            msg, session, args.realm, args.entity_id, args.source_id
                        )
                        message_count += 1
                        if message_count % 200 == 0:
                            logger.info(
                                "Processed %d MAVLink messages (last=%s, +%d envelopes)",
                                message_count,
                                msg.get_type(),
                                published,
                            )

                # Drain any pending Zenoh commands and forward them to MAVLink.
                # Done unconditionally so commands still flow when telemetry is
                # quiet. SET_MODE first (so a subsequent ARM acts on the new
                # mode), then ARM/DISARM, then continuous manual control.
                _drain_mode_queue(mav, args.target_system, mode_queue)
                _drain_arm_queue(
                    mav, args.target_system, args.target_component, arm_queue
                )
                _drain_command_queue(mav, args.target_system, cmd_queue)
        finally:
            for sub in (manual_control_sub, arm_sub, set_mode_sub):
                try:
                    sub.undeclare()
                except Exception:  # noqa: BLE001
                    pass
            if liveliness_ctx is not None:
                liveliness_ctx.__exit__(None, None, None)
            for pub in PUBLISHERS.values():
                try:
                    pub.undeclare()
                except Exception:  # noqa: BLE001
                    pass
            PUBLISHERS.clear()
            try:
                mav.close()
            except Exception:  # noqa: BLE001
                pass
    logger.info("Shutdown complete (%d messages processed)", message_count)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
