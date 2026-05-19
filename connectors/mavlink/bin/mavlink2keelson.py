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
import hashlib
import json
import logging
import math
import queue
import threading
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Optional, Tuple

import zenoh

# pymavlink dialect — ArduPilot superset of common.
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink_dialect


def _patch_pymavlink_add_message() -> None:
    # pymavlink 2.4.49 (and master at time of writing) crashes in add_message
    # when a message type is first seen without an instance-field value, then
    # later arrives with one — the "simple case" branch stores the message
    # without initializing ._instances, so the next call dereferences None.
    # Real ArduPilot Rover hits this; SITL does not.
    import copy as _copy

    def add_message(messages, mtype, msg):  # type: ignore[no-redef]
        if (
            msg._instance_field is None
            or getattr(msg, msg._instance_field, None) is None
        ):
            prev = messages.get(mtype)
            messages[mtype] = msg
            messages[mtype]._instances = (
                getattr(prev, "_instances", None) if prev is not None else None
            )
            return
        instance_value = getattr(msg, msg._instance_field)
        prev = messages.get(mtype)
        prev_instances = getattr(prev, "_instances", None) if prev is not None else None
        if prev_instances is None:
            prev_instances = {}
        prev_instances[instance_value] = msg
        messages[mtype] = _copy.copy(msg)
        messages[mtype]._instances = prev_instances
        messages["%s[%s]" % (mtype, str(instance_value))] = _copy.copy(msg)

    mavutil.add_message = add_message


_patch_pymavlink_add_message()

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
    SubjectHealth,
)
from keelson.payloads.Primitives_pb2 import (
    TimestampedBool,
    TimestampedBytes,
    TimestampedFloat,
    TimestampedInt,
    TimestampedQuaternion,
    TimestampedString,
    TimestampedTimestamp,
)
from keelson.payloads.mavlink.GoToCommand_pb2 import GoToCommand
from keelson.payloads.mavlink.RebootCommand_pb2 import RebootCommand
from keelson.payloads.mavlink.GpsInjection_pb2 import GpsInjection
from keelson.payloads.mavlink.ExternalPoseInjection_pb2 import ExternalPoseInjection
from keelson.payloads.mavlink.ExternalAttitudeInjection_pb2 import ExternalAttitudeInjection
from keelson.payloads.mavlink.DistanceSensorInjection_pb2 import DistanceSensorInjection
from keelson.payloads.mavlink.BatteryStatusInjection_pb2 import BatteryStatusInjection
from keelson.interfaces.MavlinkParam_pb2 import (
    ParamGetRequest,
    ParamSetRequest,
    ParamValueResponse,
    ParamListResponse,
    ParamSetBulkRequest,
    ParamSetBulkResponse,
    ParamSetBulkResult,
)
from keelson.interfaces.MavlinkMission_pb2 import (
    Mission,
    MissionItem,
    MissionUploadResponse,
)
from keelson.interfaces.MavlinkGeofence_pb2 import (
    Geofence,
    FenceItem,
    GeofenceUploadResponse,
)
from keelson.interfaces.MavlinkCommand_pb2 import (
    SetMessageIntervalRequest,
    SetMessageIntervalResponse,
    CommandLongRequest,
    CommandLongResponse,
)
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
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

    source = SourceHealth()
    source.name = "onboard_sensors"
    subject = SubjectHealth()
    subject.name = "sensors"
    overall = HealthLevel.HEALTH_NOMINAL

    # Walk the named bits in MAV_SYS_STATUS_SENSOR.
    for attr in dir(mavlink_dialect):
        if not attr.startswith("MAV_SYS_STATUS_SENSOR_"):
            continue
        bit = getattr(mavlink_dialect, attr)
        if not isinstance(bit, int) or bit <= 0:
            continue
        # Real sensor bits are single-bit powers of two; sentinels like
        # MAV_SYS_STATUS_SENSOR_ENUM_END are multi-bit and must be skipped.
        if bit & (bit - 1):
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
        subject.checks.append(check)

    subject.level = overall
    source.subjects.append(subject)
    source.level = overall
    payload.sources.append(source)
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
    parser.add_argument(
        "--steering-channel",
        type=int,
        default=None,
        help="RC channel to drive with manual_control.steering. "
             "If omitted, read from the autopilot's RCMAP_ROLL on first run "
             "and cached in --config-file.",
    )
    parser.add_argument(
        "--throttle-channel",
        type=int,
        default=None,
        help="RC channel to drive with manual_control.throttle. "
             "If omitted, read from the autopilot's RCMAP_THROTTLE on first "
             "run and cached in --config-file.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help="Path to the per-vehicle channel-mapping cache. Defaults to "
             "~/.keelson/mavlink-{entity_id}.json. Delete the file to force "
             "re-detection. The fingerprint inside also triggers re-detection "
             "automatically when the autopilot's servo/RC mapping changes.",
    )
    parser.add_argument(
        "--strict-rates",
        action="store_true",
        help="Raise RuntimeError when an inject_* subject's arrival rate "
             "falls below its floor or the producer goes silent. Forgiving "
             "mode (the default) just logs WARN — recommended in production "
             "since a single network hiccup would otherwise kill the "
             "connector. Strict mode is for CI / pre-deploy validation.",
    )
    return parser


# ---------------------------------------------------------------------------
# Downlink: Zenoh manual_control -> MAVLink MANUAL_CONTROL
# ---------------------------------------------------------------------------


def _send_manual_control(
    mav,
    target_system: int,
    mc: ManualControl,
    steering_channel: int = 1,
    throttle_channel: int = 3,
) -> None:
    """Translate a Keelson ManualControl payload into MAVLink RC_CHANNELS_OVERRIDE.

    Proto axes are unitless [-1.0, 1.0]; we map them to RC channel PWM values
    centered at 1500us with ±500us swing. The channels must match the
    autopilot's RCMAP_ROLL (steering) and RCMAP_THROTTLE parameters.
    ArduPilot Rover defaults: steering on RC1, throttle on RC3 — but vehicles
    with non-default wiring (e.g. throttle on RC2) need the matching
    ``--steering-channel`` / ``--throttle-channel`` CLI flags.

    Channel values of 0 (= release) are sent for other channels so we don't
    inadvertently override e.g. the mode switch on RC5/8.

    NOTE: ArduPilot drops RC overrides whose sender sysid != ``SYSID_MYGCS``
    (default 255). If ``--source-system`` is set away from the default 254,
    ``SYSID_MYGCS`` on the autopilot must match.
    """
    def _to_pwm(v: float) -> int:
        return int(round(1500 + max(-1.0, min(1.0, v)) * 500))

    chans = [0] * 8
    chans[steering_channel - 1] = _to_pwm(mc.steering)
    chans[throttle_channel - 1] = _to_pwm(mc.throttle)

    mav.mav.rc_channels_override_send(
        target_system,
        0,                       # target component (0 = any)
        chans[0], chans[1], chans[2], chans[3],
        chans[4], chans[5], chans[6], chans[7],
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
    mav,
    target_system: int,
    cmd_queue: "queue.Queue[ManualControl]",
    steering_channel: int = 1,
    throttle_channel: int = 3,
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
            _send_manual_control(
                mav, target_system, mc,
                steering_channel=steering_channel,
                throttle_channel=throttle_channel,
            )
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


# ---------------------------------------------------------------------------
# First-run autopilot introspection: read RC/servo mapping from the vehicle,
# cache it under ~/.keelson, re-detect when the autopilot configuration changes.
# ---------------------------------------------------------------------------

# Params we both fingerprint AND read for channel detection. Anything that
# changes the steering/throttle wiring belongs here; anything that doesn't
# (PIDs, EKF tuning, battery monitor, …) deliberately does not, so routine
# tuning doesn't invalidate the cache.
_FINGERPRINT_PARAMS = (
    "FRAME_CLASS", "FRAME_TYPE",
    "RCMAP_ROLL", "RCMAP_PITCH", "RCMAP_THROTTLE", "RCMAP_YAW",
) + tuple(f"SERVO{i}_FUNCTION" for i in range(1, 17))


def _read_params(
    mav, target_system: int, target_component: int,
    names: Iterable[str], timeout: float = 10.0,
) -> dict[str, float]:
    """Request each named param and collect responses. Returns only the params
    the autopilot actually answered for — caller decides how strict to be
    about missing values.

    MAVLink PARAM_REQUEST_READ/PARAM_VALUE is best-effort: under serial / UDP
    contention, individual responses are occasionally dropped, so we re-send
    requests for still-missing params every 2 s until ``timeout``.
    """
    pending = set(names)
    results: dict[str, float] = {}
    deadline = time.time() + timeout
    next_request = 0.0  # force immediate first send
    while time.time() < deadline and pending:
        if time.time() >= next_request:
            for name in pending:
                mav.mav.param_request_read_send(
                    target_system, target_component, name.encode(), -1,
                )
            next_request = time.time() + 2.0
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
        if msg is None:
            continue
        pname = msg.param_id
        if isinstance(pname, bytes):
            pname = pname.decode("utf-8", "replace")
        pname = pname.rstrip("\x00")
        if pname in pending:
            results[pname] = float(msg.param_value)
            pending.discard(pname)
    return results


def _compute_fingerprint(params: dict[str, float]) -> str:
    """Stable sha256 over the params, sorted, so the same wiring always hashes
    to the same string regardless of param-arrival order."""
    payload = json.dumps(
        {k: params[k] for k in sorted(params)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _default_config_path(entity_id: str) -> Path:
    safe = entity_id.replace("/", "_")
    return Path.home() / ".keelson" / f"mavlink-{safe}.json"


def _resolve_channels(mav, args: argparse.Namespace) -> tuple[int, int]:
    """Decide (steering_channel, throttle_channel). Precedence per-axis:
       1. CLI flag if explicitly set (not None)
       2. cached value at args.config_file, if the fingerprint still matches
       3. live detection (RCMAP_ROLL / RCMAP_THROTTLE), which is then cached

    Both axes are resolved together so we only do one param round-trip even
    when only one needs detecting.
    """
    cli_steering = args.steering_channel
    cli_throttle = args.throttle_channel
    if cli_steering is not None and cli_throttle is not None:
        logger.info(
            "Using CLI channel mapping: steering=RC%d throttle=RC%d",
            cli_steering, cli_throttle,
        )
        return cli_steering, cli_throttle

    # tlog replay has no live autopilot to answer PARAM_REQUEST_READ; fall
    # back to ArduPilot's stock channel mapping (RC1=steering, RC3=throttle).
    if args.mavlink_url.startswith("tlog:"):
        steering = cli_steering if cli_steering is not None else 1
        throttle = cli_throttle if cli_throttle is not None else 3
        logger.info(
            "tlog replay: skipping autodetect, using defaults steering=RC%d throttle=RC%d",
            steering, throttle,
        )
        return steering, throttle

    config_path = args.config_file or _default_config_path(args.entity_id)

    # Always read live params: we need them both to detect (when missing
    # from cache) and to fingerprint (to validate the cache). One round-trip.
    target_component = args.target_component or 1  # ArduPilot autopilot
    logger.info(
        "Reading autopilot params for channel mapping (target=%d/%d)...",
        args.target_system, target_component,
    )
    params = _read_params(
        mav, args.target_system, target_component, _FINGERPRINT_PARAMS,
    )
    missing = [p for p in _FINGERPRINT_PARAMS if p not in params]
    if "RCMAP_ROLL" not in params or "RCMAP_THROTTLE" not in params:
        raise RuntimeError(
            "Channel auto-detect failed: autopilot did not return "
            f"RCMAP_ROLL/RCMAP_THROTTLE (missing: {missing}). "
            "Pass --steering-channel and --throttle-channel explicitly."
        )

    fingerprint = _compute_fingerprint(params)

    if config_path.exists():
        try:
            cached = json.loads(config_path.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s (%s); re-detecting", config_path, exc)
            cached = None
        if cached and cached.get("fingerprint") == fingerprint:
            steering = int(cached["config"]["steering_channel"])
            throttle = int(cached["config"]["throttle_channel"])
            logger.info(
                "Loaded channel mapping from %s (fingerprint matches): "
                "steering=RC%d throttle=RC%d",
                config_path, steering, throttle,
            )
            return (cli_steering or steering, cli_throttle or throttle)
        if cached:
            logger.info(
                "Autopilot fingerprint changed since %s was written; re-detecting",
                config_path,
            )

    detected_steering = int(params["RCMAP_ROLL"])
    detected_throttle = int(params["RCMAP_THROTTLE"])
    payload = {
        "entity_id": args.entity_id,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "config": {
            "steering_channel": detected_steering,
            "throttle_channel": detected_throttle,
        },
        "autopilot_params": {k: params[k] for k in sorted(params)},
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n")
    logger.info(
        "Detected channel mapping (steering=RC%d throttle=RC%d), wrote %s",
        detected_steering, detected_throttle, config_path,
    )
    return (cli_steering or detected_steering, cli_throttle or detected_throttle)


# ---------------------------------------------------------------------------
# Unified downlink dispatch — pub/sub commands and sensor injection follow
# one shape: subscribe -> queue -> drain on main thread -> send MAVLink.
# ---------------------------------------------------------------------------


class DownlinkSpec(NamedTuple):
    subject: str
    payload_type: type
    send_fn: Callable[..., None]


def _autopilot_component(target_component: int) -> int:
    """ArduPilot's autopilot is component 1. When the user didn't filter
    on a specific component (target_component=0 = any), commands still need
    a real component id."""
    return target_component if target_component != 0 else 1


def _timestamp_to_usec(ts) -> int:
    """google.protobuf.Timestamp -> microseconds since UNIX epoch. Falls back
    to wall-clock if the field is unset."""
    if not ts.seconds and not ts.nanos:
        return int(time.time() * 1_000_000)
    return ts.seconds * 1_000_000 + ts.nanos // 1000


def _timestamp_to_boot_ms(ts) -> int:
    """Low-precision boot-ms-style stamp for MAVLink fields that want
    monotonic milliseconds. We pass low 32 bits of wall ms; autopilots only
    use this for ordering."""
    if not ts.seconds and not ts.nanos:
        return int(time.time() * 1000) & 0xFFFFFFFF
    return (ts.seconds * 1000 + ts.nanos // 1_000_000) & 0xFFFFFFFF


def _send_command_long(
    mav, target_system: int, target_component: int, command: int, *params: float
) -> None:
    padded = list(params) + [0.0] * (7 - len(params))
    mav.mav.command_long_send(
        target_system,
        _autopilot_component(target_component),
        command,
        0,  # confirmation
        padded[0], padded[1], padded[2], padded[3],
        padded[4], padded[5], padded[6],
    )


# ---- pub/sub command senders --------------------------------------------


def _send_goto(mav, target_system, target_component, gc: GoToCommand) -> None:
    # type_mask bits: ignore vel(3..5), accel(6..8), force(9), yaw_rate(11)
    type_mask = 0b0000_1111_1111_1000
    if not gc.HasField("yaw_deg"):
        type_mask |= 1 << 10
    yaw_rad = math.radians(gc.yaw_deg) if gc.HasField("yaw_deg") else 0.0
    alt = gc.altitude_msl_m if gc.HasField("altitude_msl_m") else 0.0
    mav.mav.set_position_target_global_int_send(
        0,  # time_boot_ms
        target_system,
        _autopilot_component(target_component),
        mavlink_dialect.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        type_mask,
        int(gc.latitude * 1e7),
        int(gc.longitude * 1e7),
        alt,
        0.0, 0.0, 0.0,
        0.0, 0.0, 0.0,
        yaw_rad, 0.0,
    )
    if gc.HasField("ground_speed_mps"):
        _send_command_long(
            mav, target_system, target_component,
            mavlink_dialect.MAV_CMD_DO_CHANGE_SPEED,
            1.0,                       # ground speed
            gc.ground_speed_mps, -1.0, 0.0,
        )


def _send_set_cruise_speed(mav, target_system, target_component, tf: TimestampedFloat) -> None:
    _send_command_long(
        mav, target_system, target_component,
        mavlink_dialect.MAV_CMD_DO_CHANGE_SPEED,
        1.0, tf.value, -1.0, 0.0,
    )


def _send_set_current_waypoint(mav, target_system, target_component, ti: TimestampedInt) -> None:
    mav.mav.mission_set_current_send(
        target_system, _autopilot_component(target_component), int(ti.value),
    )


def _send_emergency_stop(mav, target_system, target_component, tb: TimestampedBool) -> None:
    if not tb.value:
        return
    _send_command_long(
        mav, target_system, target_component,
        mavlink_dialect.MAV_CMD_DO_FLIGHTTERMINATION, 1.0,
    )


def _send_enable_geofence(mav, target_system, target_component, tb: TimestampedBool) -> None:
    _send_command_long(
        mav, target_system, target_component,
        mavlink_dialect.MAV_CMD_DO_FENCE_ENABLE,
        1.0 if tb.value else 0.0,
    )


def _send_clear_mission(mav, target_system, target_component, tb: TimestampedBool) -> None:
    if not tb.value:
        return
    mav.mav.mission_clear_all_send(
        target_system, _autopilot_component(target_component),
    )


def _send_save_params(mav, target_system, target_component, tb: TimestampedBool) -> None:
    if not tb.value:
        return
    # MAV_CMD_PREFLIGHT_STORAGE: param1=1 (write params), others -1 (ignore)
    _send_command_long(
        mav, target_system, target_component,
        mavlink_dialect.MAV_CMD_PREFLIGHT_STORAGE,
        1.0, -1.0, -1.0, -1.0,
    )


def _send_reboot(mav, target_system, target_component, rc: RebootCommand) -> None:
    action_to_p1 = {
        RebootCommand.REBOOT: 1.0,
        RebootCommand.SHUTDOWN: 2.0,
        RebootCommand.REBOOT_TO_BOOTLOADER: 3.0,
    }
    p1 = action_to_p1.get(rc.action)
    if p1 is None:
        raise ValueError(f"cmd_reboot: unspecified action {rc.action}")
    # param1=autopilot action, param2=companion action (0=do nothing)
    _send_command_long(
        mav, target_system, target_component,
        mavlink_dialect.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
        p1, 0.0,
    )


# ---- injection senders ---------------------------------------------------


# GPS_INPUT ignore-flag bitmask values (per MAVLink common.xml).
_GPS_IGN_ALT = 1
_GPS_IGN_HDOP = 2
_GPS_IGN_VDOP = 4
_GPS_IGN_VEL_H = 8
_GPS_IGN_VEL_V = 16
_GPS_IGN_SPEED_ACC = 32
_GPS_IGN_HACC = 64
_GPS_IGN_VACC = 128


def _send_inject_gps(mav, target_system, target_component, gi: GpsInjection) -> None:
    ignore = 0
    if not gi.HasField("hdop"): ignore |= _GPS_IGN_HDOP
    if not gi.HasField("vdop"): ignore |= _GPS_IGN_VDOP
    if not (gi.HasField("velocity_north_mps") and gi.HasField("velocity_east_mps")):
        ignore |= _GPS_IGN_VEL_H
    if not gi.HasField("velocity_down_mps"): ignore |= _GPS_IGN_VEL_V
    if not gi.HasField("speed_accuracy_mps"): ignore |= _GPS_IGN_SPEED_ACC
    if not gi.HasField("horiz_accuracy_m"): ignore |= _GPS_IGN_HACC
    if not gi.HasField("vert_accuracy_m"): ignore |= _GPS_IGN_VACC

    mav.mav.gps_input_send(
        _timestamp_to_usec(gi.timestamp),
        0,                                  # gps_id (primary)
        ignore,
        0, 0,                               # time_week_ms, time_week (unused)
        gi.fix_type,
        int(gi.latitude * 1e7),
        int(gi.longitude * 1e7),
        gi.altitude_msl_m,
        gi.hdop if gi.HasField("hdop") else 0.0,
        gi.vdop if gi.HasField("vdop") else 0.0,
        gi.velocity_north_mps if gi.HasField("velocity_north_mps") else 0.0,
        gi.velocity_east_mps if gi.HasField("velocity_east_mps") else 0.0,
        gi.velocity_down_mps if gi.HasField("velocity_down_mps") else 0.0,
        gi.speed_accuracy_mps if gi.HasField("speed_accuracy_mps") else 0.0,
        gi.horiz_accuracy_m if gi.HasField("horiz_accuracy_m") else 0.0,
        gi.vert_accuracy_m if gi.HasField("vert_accuracy_m") else 0.0,
        gi.satellites_visible,
    )


_RTCM_SEQ = 0
_RTCM_MAX_PAYLOAD = 180


def _send_inject_rtcm(mav, target_system, target_component, tb: TimestampedBytes) -> None:
    global _RTCM_SEQ
    data = bytes(tb.value)
    if not data:
        return
    if len(data) <= _RTCM_MAX_PAYLOAD:
        padded = data.ljust(_RTCM_MAX_PAYLOAD, b"\x00")
        mav.mav.gps_rtcm_data_send(0, len(data), padded)
        return
    # Fragmented: up to 4 fragments per MAVLink GPS_RTCM_DATA contract
    _RTCM_SEQ = (_RTCM_SEQ + 1) & 0x1F
    seq = _RTCM_SEQ
    chunks = [data[i:i + _RTCM_MAX_PAYLOAD]
              for i in range(0, len(data), _RTCM_MAX_PAYLOAD)][:4]
    for frag_id, chunk in enumerate(chunks):
        flags = ((seq & 0x1F) << 3) | ((frag_id & 0x3) << 1) | 0x1
        padded = chunk.ljust(_RTCM_MAX_PAYLOAD, b"\x00")
        mav.mav.gps_rtcm_data_send(flags, len(chunk), padded)


def _send_inject_velocity_body_mps(
    mav, target_system, target_component, vec: Decomposed3DVector
) -> None:
    mav.mav.vision_speed_estimate_send(
        _timestamp_to_usec(vec.timestamp),
        vec.vector.x, vec.vector.y, vec.vector.z,
        [],  # covariance: empty = autopilot uses defaults
    )


def _quat_to_euler(orientation) -> Tuple[float, float, float]:
    """foxglove.Quaternion (w,x,y,z) -> (roll, pitch, yaw) in radians, ZYX intrinsic.
    Self-contained to avoid pulling squaternion just for this path."""
    w, x, y, z = orientation.w, orientation.x, orientation.y, orientation.z
    # roll (x-axis rotation)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    # pitch (y-axis rotation), clamped to avoid asin domain errors
    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2, sinp) if abs(sinp) >= 1 else math.asin(sinp)
    # yaw (z-axis rotation)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


def _send_inject_external_pose(
    mav, target_system, target_component, ep: ExternalPoseInjection
) -> None:
    if ep.HasField("orientation"):
        roll, pitch, yaw = _quat_to_euler(ep.orientation)
    else:
        roll = pitch = yaw = 0.0
    cov = list(ep.covariance) if ep.covariance else [float("nan")]
    mav.mav.vision_position_estimate_send(
        _timestamp_to_usec(ep.timestamp),
        ep.x_m, ep.y_m, ep.z_m,
        roll, pitch, yaw,
        cov,
    )


def _send_inject_external_attitude(
    mav, target_system, target_component, ea: ExternalAttitudeInjection
) -> None:
    q = [ea.orientation.w, ea.orientation.x, ea.orientation.y, ea.orientation.z]
    cov = list(ea.covariance) if ea.covariance else [float("nan")]
    mav.mav.att_pos_mocap_send(
        _timestamp_to_usec(ea.timestamp),
        q,
        0.0, 0.0, 0.0,
        cov,
    )


def _send_inject_distance_sensor(
    mav, target_system, target_component, ds: DistanceSensorInjection
) -> None:
    # DISTANCE_SENSOR units are cm.
    current_cm = max(0, int(ds.current_distance_m * 100))
    min_cm = max(0, int(ds.min_distance_m * 100))
    max_cm = max(0, int(ds.max_distance_m * 100))
    mav.mav.distance_sensor_send(
        _timestamp_to_boot_ms(ds.timestamp),
        min_cm, max_cm, current_cm,
        ds.sensor_type,
        ds.sensor_id,
        ds.orientation,
        0,  # covariance (cm); 0 = unknown
    )


def _send_inject_battery_status(
    mav, target_system, target_component, bs: BatteryStatusInjection
) -> None:
    cells = list(bs.cell_voltages_mv)
    while len(cells) < 14:
        cells.append(65535)  # MAVLink sentinel for "unknown"
    mav.mav.battery_status_send(
        bs.battery_id,
        bs.function if bs.HasField("function") else 0,
        bs.type if bs.HasField("type") else 0,
        bs.temperature_centidegc if bs.HasField("temperature_centidegc") else 32767,
        cells[:10],
        bs.current_centiamps if bs.HasField("current_centiamps") else -1,
        bs.consumed_mah if bs.HasField("consumed_mah") else -1,
        -1,                                # energy_consumed
        bs.state_of_charge_pct if bs.HasField("state_of_charge_pct") else -1,
    )


def _send_inject_system_time(
    mav, target_system, target_component, tt: TimestampedTimestamp
) -> None:
    if tt.HasField("value") and (tt.value.seconds or tt.value.nanos):
        unix_usec = tt.value.seconds * 1_000_000 + tt.value.nanos // 1000
    else:
        unix_usec = int(time.time() * 1_000_000)
    mav.mav.system_time_send(unix_usec, 0)


DOWNLINK_COMMANDS: list[DownlinkSpec] = [
    DownlinkSpec("cmd_goto",                  GoToCommand,          _send_goto),
    DownlinkSpec("cmd_set_cruise_speed",      TimestampedFloat,     _send_set_cruise_speed),
    DownlinkSpec("cmd_set_current_waypoint",  TimestampedInt,       _send_set_current_waypoint),
    DownlinkSpec("cmd_emergency_stop",        TimestampedBool,      _send_emergency_stop),
    DownlinkSpec("cmd_enable_geofence",       TimestampedBool,      _send_enable_geofence),
    DownlinkSpec("cmd_clear_mission",         TimestampedBool,      _send_clear_mission),
    DownlinkSpec("cmd_save_params",           TimestampedBool,      _send_save_params),
    DownlinkSpec("cmd_reboot",                RebootCommand,        _send_reboot),
]


DOWNLINK_INJECTIONS: list[DownlinkSpec] = [
    DownlinkSpec("inject_gps",                GpsInjection,                _send_inject_gps),
    DownlinkSpec("inject_rtcm",               TimestampedBytes,            _send_inject_rtcm),
    DownlinkSpec("inject_velocity_body_mps",  Decomposed3DVector,          _send_inject_velocity_body_mps),
    DownlinkSpec("inject_external_pose",      ExternalPoseInjection,       _send_inject_external_pose),
    DownlinkSpec("inject_external_attitude",  ExternalAttitudeInjection,   _send_inject_external_attitude),
    DownlinkSpec("inject_distance_sensor",    DistanceSensorInjection,     _send_inject_distance_sensor),
    DownlinkSpec("inject_battery_status",     BatteryStatusInjection,      _send_inject_battery_status),
    DownlinkSpec("inject_system_time",        TimestampedTimestamp,        _send_inject_system_time),
]


# Per-subject (floor, ceiling) rates in Hz. ArduPilot's EKF expects each
# injection at a sensor-type-specific cadence; well below the floor it will
# starve / fall back to default sources, well above the ceiling we're just
# wasting MAVLink bandwidth without changing fusion outcome. Numbers are
# heuristics — actual EKF tolerance depends on EK3_SRC* weighting — but
# they're a good default health signal.
INJECTION_RATE_LIMITS: dict[str, Tuple[float, float]] = {
    "inject_gps":                (5.0,  20.0),
    "inject_rtcm":               (0.1, 100.0),  # base-rate-dependent, very loose
    "inject_velocity_body_mps":  (10.0, 100.0),
    "inject_external_pose":      (10.0, 100.0),
    "inject_external_attitude":  (10.0, 100.0),
    "inject_distance_sensor":    (10.0, 100.0),
    "inject_battery_status":     (1.0,  20.0),
    "inject_system_time":        (0.5,   5.0),
}


class RateMonitor:
    """Observes per-subject arrival rates over a rolling window and reports
    deviations from each subject's (floor, ceiling) band.

    Two modes:
      - forgiving (default): logs WARN on floor violations / unexpected
        silence, INFO on ceiling violations. The connector keeps forwarding.
      - strict (``--strict-rates``): raises RuntimeError on floor / silent
        transitions. Suitable for CI / pre-deploy validation; not for
        production, where a brief network hiccup would kill the connector.

    Hysteresis: state transitions are reported once per episode, not per
    sample, so a noisy producer doesn't spam the log.
    """

    WINDOW_S = 5.0
    MIN_OBSERVATION_S = 3.0
    CHECK_PERIOD_S = 2.0
    SILENT_MULTIPLIER = 3.0  # gap >= 3 * WINDOW_S → "silent"

    def __init__(self, strict: bool = False) -> None:
        self._lock = threading.Lock()
        self._arrivals: dict[str, deque] = defaultdict(lambda: deque(maxlen=2048))
        self._first_sample_at: dict[str, float] = {}
        # State: "ok" | "below_floor" | "above_ceiling" | "silent"
        self._state: dict[str, str] = {}
        self._strict = strict
        self._last_check_at = 0.0

    def record(self, subject: str) -> None:
        if subject not in INJECTION_RATE_LIMITS:
            return
        now = time.time()
        with self._lock:
            if subject not in self._first_sample_at:
                self._first_sample_at[subject] = now
            dq = self._arrivals[subject]
            dq.append(now)
            cutoff = now - self.WINDOW_S
            while dq and dq[0] < cutoff:
                dq.popleft()

    def check(self) -> None:
        """Walk every observed injection subject and emit warnings / raise
        if rates have crossed a state boundary. Internally rate-limited so
        callers can invoke this every main-loop iteration without overhead.
        """
        now = time.time()
        if now - self._last_check_at < self.CHECK_PERIOD_S:
            return
        self._last_check_at = now

        with self._lock:
            observed_subjects = list(self._first_sample_at.keys())

        for subject in observed_subjects:
            floor, ceiling = INJECTION_RATE_LIMITS[subject]
            with self._lock:
                first_at = self._first_sample_at.get(subject, now)
                dq = list(self._arrivals.get(subject, ()))
            elapsed = now - first_at
            if elapsed < self.MIN_OBSERVATION_S:
                continue

            last_sample = dq[-1] if dq else first_at
            silence = now - last_sample

            new_state: str
            rate = 0.0
            if silence > self.SILENT_MULTIPLIER * self.WINDOW_S:
                new_state = "silent"
            else:
                window = min(self.WINDOW_S, elapsed)
                rate = len(dq) / window if window > 0 else 0.0
                if rate < floor:
                    new_state = "below_floor"
                elif rate > ceiling:
                    new_state = "above_ceiling"
                else:
                    new_state = "ok"

            old_state = self._state.get(subject, "ok")
            if new_state == old_state:
                continue
            self._state[subject] = new_state

            if new_state == "ok":
                logger.info(
                    "%s rate recovered to %.1f Hz (target [%.1f, %.1f])",
                    subject, rate, floor, ceiling,
                )
            elif new_state == "below_floor":
                msg = (
                    f"{subject} rate {rate:.1f} Hz below floor {floor:.1f} Hz "
                    f"— ArduPilot's EKF may starve on this signal"
                )
                if self._strict:
                    raise RuntimeError(msg)
                logger.warning(msg)
            elif new_state == "above_ceiling":
                logger.info(
                    "%s rate %.1f Hz exceeds ceiling %.1f Hz "
                    "(wasting bandwidth; not an error)",
                    subject, rate, ceiling,
                )
            elif new_state == "silent":
                msg = (
                    f"{subject} has not produced a sample for {silence:.1f} s "
                    f"after initially streaming — producer dead?"
                )
                if self._strict:
                    raise RuntimeError(msg)
                logger.warning(msg)


def _install_pubsub_downlink(
    session: "zenoh.Session",
    args: argparse.Namespace,
    spec: DownlinkSpec,
    dispatch_queue: "queue.Queue",
    rate_monitor: Optional[RateMonitor] = None,
) -> "zenoh.Subscriber":
    key = keelson.construct_pubsub_key(args.realm, args.entity_id, spec.subject, "**")

    def _on_sample(sample: "zenoh.Sample") -> None:
        try:
            _, _, payload_bytes = keelson.uncover(bytes(sample.payload.to_bytes()))
            msg = spec.payload_type()
            msg.ParseFromString(payload_bytes)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decode %s envelope", spec.subject)
            return
        try:
            dispatch_queue.put_nowait((spec, msg))
            logger.debug("Queued %s", spec.subject)
        except queue.Full:
            logger.warning("%s queue full; dropping command", spec.subject)
        if rate_monitor is not None:
            rate_monitor.record(spec.subject)

    logger.info("Subscribing to %s for %s", key, spec.subject)
    return session.declare_subscriber(key, _on_sample)


def _drain_pubsub_dispatch(
    mav, target_system: int, target_component: int,
    dispatch_queue: "queue.Queue",
) -> int:
    handled = 0
    while True:
        try:
            spec, msg = dispatch_queue.get_nowait()
        except queue.Empty:
            return handled
        try:
            spec.send_fn(mav, target_system, target_component, msg)
            handled += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed to forward %s", spec.subject)


# ---------------------------------------------------------------------------
# RPC dispatch — request/response procedures (params, mission, geofence,
# message-interval, command_long escape hatch). Queryable callbacks run on
# Zenoh's IO thread; the actual MAVLink work happens on the main thread via
# a unified rpc_queue.
# ---------------------------------------------------------------------------


class RpcOp(NamedTuple):
    query: Any                          # zenoh.Query
    procedure: str
    reply_key: str
    request_bytes: bytes


RPC_PROCEDURES = (
    "get_param", "set_param", "list_params", "set_params",
    "set_message_interval", "send_command_long",
    "upload_mission", "download_mission", "upload_geofence",
)


def _make_rpc_handler(procedure: str, reply_key: str, rpc_queue: "queue.Queue[RpcOp]"):
    def _handler(query) -> None:
        try:
            payload = query.payload
            request_bytes = bytes(payload.to_bytes()) if payload is not None else b""
        except Exception:  # noqa: BLE001
            request_bytes = b""
        try:
            rpc_queue.put_nowait(RpcOp(
                query=query, procedure=procedure,
                reply_key=reply_key, request_bytes=request_bytes,
            ))
            logger.debug("Queued RPC %s", procedure)
        except queue.Full:
            logger.warning("RPC queue full; rejecting %s", procedure)
            _reply_err(query, "RPC queue full")
    return _handler


def _reply_err(query, msg: str) -> None:
    try:
        query.reply_err(ErrorResponse(error_description=msg).SerializeToString())
    except Exception:  # noqa: BLE001
        logger.exception("Failed to reply_err on RPC")


def _setup_rpc_queryables(
    session: "zenoh.Session",
    args: argparse.Namespace,
    rpc_queue: "queue.Queue[RpcOp]",
) -> list:
    queryables = []
    for proc in RPC_PROCEDURES:
        key = keelson.construct_rpc_key(args.realm, args.entity_id, proc, args.source_id)
        q = session.declare_queryable(key, _make_rpc_handler(proc, key, rpc_queue), complete=True)
        logger.info("Declared RPC queryable: %s", key)
        queryables.append(q)
    return queryables


# ---- RPC handlers --------------------------------------------------------


def _read_params_typed(
    mav, target_system: int, target_component: int,
    names: Iterable[str], timeout: float = 3.0,
) -> dict[str, Tuple[float, int]]:
    """Like _read_params but also returns the MAV_PARAM_TYPE per param so RPC
    callers can detect autopilot-side type coercion. Re-requests still-pending
    params every 2 s to tolerate dropped PARAM_REQUEST / PARAM_VALUE frames."""
    pending = set(names)
    results: dict[str, Tuple[float, int]] = {}
    deadline = time.time() + timeout
    next_request = 0.0
    while time.time() < deadline and pending:
        if time.time() >= next_request:
            for name in pending:
                mav.mav.param_request_read_send(
                    target_system, target_component, name.encode(), -1,
                )
            next_request = time.time() + 2.0
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
        if msg is None:
            continue
        pname = msg.param_id
        if isinstance(pname, bytes):
            pname = pname.decode("utf-8", "replace")
        pname = pname.rstrip("\x00")
        if pname in pending:
            results[pname] = (float(msg.param_value), int(msg.param_type))
            pending.discard(pname)
    return results


def _handle_get_param(mav, args, op: RpcOp, target_component: int) -> None:
    req = ParamGetRequest()
    req.ParseFromString(op.request_bytes)
    if not req.name:
        _reply_err(op.query, "get_param: 'name' is required")
        return
    results = _read_params_typed(
        mav, args.target_system, target_component, [req.name], timeout=2.0,
    )
    if req.name not in results:
        _reply_err(op.query, f"get_param: no PARAM_VALUE for {req.name!r} within 2s")
        return
    value, ptype = results[req.name]
    op.query.reply(op.reply_key, ParamValueResponse(
        name=req.name, value=value, mav_param_type=ptype,
    ).SerializeToString())


def _handle_set_param(mav, args, op: RpcOp, target_component: int) -> None:
    req = ParamSetRequest()
    req.ParseFromString(op.request_bytes)
    if not req.name:
        _reply_err(op.query, "set_param: 'name' is required")
        return
    mav.mav.param_set_send(
        args.target_system, target_component,
        req.name.encode(), float(req.value),
        mavlink_dialect.MAV_PARAM_TYPE_REAL32,
    )
    results = _read_params_typed(
        mav, args.target_system, target_component, [req.name], timeout=2.0,
    )
    if req.name not in results:
        _reply_err(op.query, f"set_param: write of {req.name!r} not confirmed within 2s")
        return
    value, ptype = results[req.name]
    op.query.reply(op.reply_key, ParamValueResponse(
        name=req.name, value=value, mav_param_type=ptype,
    ).SerializeToString())


def _handle_list_params(mav, args, op: RpcOp, target_component: int) -> None:
    mav.mav.param_request_list_send(args.target_system, target_component)
    collected: dict[str, Tuple[float, int]] = {}
    total: Optional[int] = None
    deadline = time.time() + 30.0
    last_msg_at = time.time()
    while time.time() < deadline:
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
        if msg is None:
            if collected and time.time() - last_msg_at > 2.0:
                break
            continue
        last_msg_at = time.time()
        pname = msg.param_id
        if isinstance(pname, bytes):
            pname = pname.decode("utf-8", "replace")
        pname = pname.rstrip("\x00")
        collected[pname] = (float(msg.param_value), int(msg.param_type))
        if total is None:
            total = int(msg.param_count)
        if total and len(collected) >= total:
            break
    resp = ParamListResponse()
    for name in sorted(collected):
        value, ptype = collected[name]
        item = resp.params.add()
        item.name = name
        item.value = value
        item.mav_param_type = ptype
    op.query.reply(op.reply_key, resp.SerializeToString())


def _handle_set_params(mav, args, op: RpcOp, target_component: int) -> None:
    req = ParamSetBulkRequest()
    req.ParseFromString(op.request_bytes)
    resp = ParamSetBulkResponse()
    for set_req in req.params:
        result: ParamSetBulkResult = resp.results.add()
        result.name = set_req.name
        try:
            mav.mav.param_set_send(
                args.target_system, target_component,
                set_req.name.encode(), float(set_req.value),
                mavlink_dialect.MAV_PARAM_TYPE_REAL32,
            )
            echoed = _read_params_typed(
                mav, args.target_system, target_component, [set_req.name], timeout=2.0,
            )
            if set_req.name in echoed:
                value, ptype = echoed[set_req.name]
                result.ok = True
                result.value = value
                result.mav_param_type = ptype
            else:
                result.ok = False
                result.error = "write not confirmed within 2s"
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.error = str(exc)
    op.query.reply(op.reply_key, resp.SerializeToString())


def _handle_set_message_interval(mav, args, op: RpcOp, target_component: int) -> None:
    req = SetMessageIntervalRequest()
    req.ParseFromString(op.request_bytes)
    if req.message_id != 0:
        msg_id = req.message_id
    elif req.message_name:
        msg_id = getattr(mavlink_dialect, f"MAVLINK_MSG_ID_{req.message_name.upper()}", None)
        if msg_id is None:
            _reply_err(op.query,
                f"set_message_interval: unknown message {req.message_name!r}")
            return
    else:
        _reply_err(op.query,
            "set_message_interval: either message_id or message_name is required")
        return
    interval_us = -1.0 if req.hz <= 0 else 1_000_000.0 / req.hz
    _send_command_long(
        mav, args.target_system, target_component,
        mavlink_dialect.MAV_CMD_SET_MESSAGE_INTERVAL,
        float(msg_id), float(interval_us),
    )
    ack = mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=2.0)
    op.query.reply(op.reply_key, SetMessageIntervalResponse(
        accepted=(ack is not None and ack.result == 0),
        mav_result=int(ack.result) if ack is not None else -1,
    ).SerializeToString())


def _handle_send_command_long(mav, args, op: RpcOp, target_component: int) -> None:
    req = CommandLongRequest()
    req.ParseFromString(op.request_bytes)
    tc = req.target_component if req.HasField("target_component") else target_component
    mav.mav.command_long_send(
        args.target_system, _autopilot_component(tc),
        req.command, 0,
        req.param1, req.param2, req.param3, req.param4,
        req.param5, req.param6, req.param7,
    )
    ack = mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=3.0)
    op.query.reply(op.reply_key, CommandLongResponse(
        mav_result=int(ack.result) if ack is not None else -1,
        text="" if ack is not None else "no COMMAND_ACK received within 3s",
    ).SerializeToString())


# ---- Mission / fence protocol -------------------------------------------


def _missionitem_to_dict(mi: MissionItem) -> dict:
    return {
        "seq": mi.seq, "frame": mi.frame, "command": mi.command,
        "current": mi.current, "autocontinue": mi.autocontinue,
        "param1": mi.param1, "param2": mi.param2, "param3": mi.param3, "param4": mi.param4,
        "x": mi.x, "y": mi.y, "z": mi.z,
        "mission_type": mi.mission_type,
    }


def _fenceitem_to_dict(fi: FenceItem) -> dict:
    return {
        "seq": fi.seq, "frame": fi.frame, "command": fi.command,
        "current": False, "autocontinue": False,
        "param1": fi.param1, "param2": fi.param2, "param3": fi.param3, "param4": fi.param4,
        "x": fi.x, "y": fi.y, "z": fi.z,
        "mission_type": 1,  # MAV_MISSION_TYPE_FENCE
    }


def _upload_mission_items(
    mav, target_system: int, target_component: int,
    items: list[dict], mission_type: int, timeout: float = 30.0,
) -> Tuple[bool, int, str]:
    """Run the MAVLink mission upload protocol. Returns (accepted, mission_result, error)."""
    tc = _autopilot_component(target_component)
    count = len(items)
    mav.mav.mission_count_send(target_system, tc, count, mission_type)
    if count == 0:
        # Some autopilots send an immediate ACK when count=0
        ack = mav.recv_match(type="MISSION_ACK", blocking=True, timeout=5.0)
        if ack is None:
            return False, -1, "no MISSION_ACK after empty MISSION_COUNT"
        return (ack.type == 0, int(ack.type),
                "" if ack.type == 0 else f"MAV_MISSION_RESULT={ack.type}")

    deadline = time.time() + timeout
    requested: set[int] = set()
    while time.time() < deadline and len(requested) < count:
        msg = mav.recv_match(
            type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
            blocking=True, timeout=2.0,
        )
        if msg is None:
            continue
        if msg.get_type() == "MISSION_ACK":
            return (msg.type == 0, int(msg.type),
                    "" if msg.type == 0 else f"MAV_MISSION_RESULT={msg.type}")
        seq = int(msg.seq)
        if seq >= count:
            continue
        item = items[seq]
        mav.mav.mission_item_int_send(
            target_system, tc,
            seq,
            int(item["frame"]),
            int(item["command"]),
            1 if item.get("current") else 0,
            1 if item.get("autocontinue") else 0,
            float(item["param1"]), float(item["param2"]),
            float(item["param3"]), float(item["param4"]),
            int(item["x"]), int(item["y"]), float(item["z"]),
            mission_type,
        )
        requested.add(seq)
    # Wait for final MISSION_ACK
    ack_deadline = time.time() + 5.0
    while time.time() < ack_deadline:
        msg = mav.recv_match(type="MISSION_ACK", blocking=True, timeout=2.0)
        if msg is not None:
            return (msg.type == 0, int(msg.type),
                    "" if msg.type == 0 else f"MAV_MISSION_RESULT={msg.type}")
    return False, -1, "no MISSION_ACK after full upload"


def _download_mission_items(
    mav, target_system: int, target_component: int,
    mission_type: int, timeout: float = 30.0,
) -> list[dict]:
    tc = _autopilot_component(target_component)
    mav.mav.mission_request_list_send(target_system, tc, mission_type)
    count_msg = mav.recv_match(type="MISSION_COUNT", blocking=True, timeout=3.0)
    if count_msg is None:
        return []
    count = int(count_msg.count)
    items: list[dict] = []
    deadline = time.time() + timeout
    for seq in range(count):
        if time.time() > deadline:
            break
        mav.mav.mission_request_int_send(target_system, tc, seq, mission_type)
        item_msg = mav.recv_match(type="MISSION_ITEM_INT", blocking=True, timeout=2.0)
        if item_msg is None:
            break
        items.append({
            "seq": int(item_msg.seq),
            "frame": int(item_msg.frame),
            "command": int(item_msg.command),
            "current": bool(item_msg.current),
            "autocontinue": bool(item_msg.autocontinue),
            "param1": float(item_msg.param1), "param2": float(item_msg.param2),
            "param3": float(item_msg.param3), "param4": float(item_msg.param4),
            "x": int(item_msg.x), "y": int(item_msg.y), "z": float(item_msg.z),
            "mission_type": int(getattr(item_msg, "mission_type", mission_type)),
        })
    mav.mav.mission_ack_send(target_system, tc, 0, mission_type)
    return items


def _handle_upload_mission(mav, args, op: RpcOp, target_component: int) -> None:
    req = Mission()
    req.ParseFromString(op.request_bytes)
    items = [_missionitem_to_dict(mi) for mi in req.items]
    accepted, result, error = _upload_mission_items(
        mav, args.target_system, target_component, items, mission_type=0,
    )
    op.query.reply(op.reply_key, MissionUploadResponse(
        accepted=accepted, mission_result=result, error=error,
    ).SerializeToString())


def _handle_download_mission(mav, args, op: RpcOp, target_component: int) -> None:
    items = _download_mission_items(
        mav, args.target_system, target_component, mission_type=0,
    )
    resp = Mission()
    for d in items:
        mi = resp.items.add()
        mi.seq = d["seq"]; mi.frame = d["frame"]; mi.command = d["command"]
        mi.current = d["current"]; mi.autocontinue = d["autocontinue"]
        mi.param1 = d["param1"]; mi.param2 = d["param2"]
        mi.param3 = d["param3"]; mi.param4 = d["param4"]
        mi.x = d["x"]; mi.y = d["y"]; mi.z = d["z"]
        mi.mission_type = d["mission_type"]
    op.query.reply(op.reply_key, resp.SerializeToString())


def _handle_upload_geofence(mav, args, op: RpcOp, target_component: int) -> None:
    req = Geofence()
    req.ParseFromString(op.request_bytes)
    items = [_fenceitem_to_dict(fi) for fi in req.items]
    accepted, result, error = _upload_mission_items(
        mav, args.target_system, target_component, items, mission_type=1,
    )
    op.query.reply(op.reply_key, GeofenceUploadResponse(
        accepted=accepted, mission_result=result, error=error,
    ).SerializeToString())


def _drain_rpc_queue(
    mav, args, rpc_queue: "queue.Queue[RpcOp]", target_component: int,
) -> int:
    handled = 0
    handlers = {
        "get_param": _handle_get_param,
        "set_param": _handle_set_param,
        "list_params": _handle_list_params,
        "set_params": _handle_set_params,
        "set_message_interval": _handle_set_message_interval,
        "send_command_long": _handle_send_command_long,
        "upload_mission": _handle_upload_mission,
        "download_mission": _handle_download_mission,
        "upload_geofence": _handle_upload_geofence,
    }
    while True:
        try:
            op = rpc_queue.get_nowait()
        except queue.Empty:
            return handled
        fn = handlers.get(op.procedure)
        if fn is None:
            _reply_err(op.query, f"unknown RPC procedure: {op.procedure}")
            continue
        try:
            fn(mav, args, op, target_component)
            handled += 1
        except Exception:  # noqa: BLE001
            logger.exception("RPC %s handler failed", op.procedure)
            _reply_err(op.query, traceback.format_exc())


def run(args: argparse.Namespace) -> int:
    conf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)

    mav = open_mavlink(
        args.mavlink_url, args.source_system, args.source_component, args.baud
    )

    # Wait for the first HEARTBEAT before reading params — the autopilot may
    # not respond to PARAM_REQUEST_READ while it's still booting.
    logger.info("Waiting for HEARTBEAT before channel auto-detect...")
    hb = mav.wait_heartbeat(timeout=15)
    if hb is None:
        raise RuntimeError("No HEARTBEAT received within 15s")

    args.steering_channel, args.throttle_channel = _resolve_channels(mav, args)

    cmd_queue: "queue.Queue[ManualControl]" = queue.Queue(maxsize=1024)
    arm_queue: "queue.Queue[bool]" = queue.Queue(maxsize=64)
    mode_queue: "queue.Queue[str]" = queue.Queue(maxsize=64)
    # Unified queue for the new pub/sub command + injection family.
    pubsub_dispatch_queue: "queue.Queue[Tuple[DownlinkSpec, Any]]" = queue.Queue(maxsize=4096)
    # Queue for incoming RPC requests, drained on the main thread.
    rpc_queue: "queue.Queue[RpcOp]" = queue.Queue(maxsize=64)

    rate_monitor = RateMonitor(strict=args.strict_rates)
    if args.strict_rates:
        logger.info(
            "Strict rate monitoring enabled — connector will raise on "
            "inject_* floor / silence violations"
        )

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session, GracefulShutdown() as shutdown:
        manual_control_sub = _setup_manual_control_subscriber(
            session, args, cmd_queue
        )
        arm_sub = _setup_arm_subscriber(session, args, arm_queue)
        set_mode_sub = _setup_set_mode_subscriber(session, args, mode_queue)

        # New pub/sub downlinks (commands + injection) via the unified factory.
        new_downlink_subs = [
            _install_pubsub_downlink(
                session, args, spec, pubsub_dispatch_queue,
                rate_monitor=rate_monitor,
            )
            for spec in (*DOWNLINK_COMMANDS, *DOWNLINK_INJECTIONS)
        ]

        # RPC queryables (params, mission, geofence, misc).
        rpc_queryables = _setup_rpc_queryables(session, args, rpc_queue)

        liveliness_ctx: Optional[Any] = None
        liveliness_token = None
        message_count = 0

        try:
            recv_error_count = 0
            while not shutdown.is_requested():
                # Block briefly so we can check for shutdown periodically.
                # Catch pymavlink parsing errors so one bad message doesn't
                # kill the entire connector.
                try:
                    msg = mav.recv_match(blocking=True, timeout=args.recv_timeout)
                except Exception as exc:  # noqa: BLE001
                    recv_error_count += 1
                    if recv_error_count <= 5 or recv_error_count % 100 == 0:
                        logger.warning(
                            "pymavlink recv_match raised %s: %s (count=%d)",
                            type(exc).__name__,
                            exc,
                            recv_error_count,
                        )
                    msg = None

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
                _drain_command_queue(
                    mav, args.target_system, cmd_queue,
                    steering_channel=args.steering_channel,
                    throttle_channel=args.throttle_channel,
                )
                _drain_pubsub_dispatch(
                    mav, args.target_system, args.target_component,
                    pubsub_dispatch_queue,
                )
                _drain_rpc_queue(
                    mav, args, rpc_queue, args.target_component,
                )
                # Internally rate-limited; safe to call every iteration.
                # In --strict-rates mode this raises and tears down the loop.
                rate_monitor.check()
        finally:
            for sub in (manual_control_sub, arm_sub, set_mode_sub, *new_downlink_subs):
                try:
                    sub.undeclare()
                except Exception:  # noqa: BLE001
                    pass
            for q in rpc_queryables:
                try:
                    q.undeclare()
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
