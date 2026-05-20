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
import os
import queue
import threading
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Optional, Tuple

import zenoh

# pymavlink dialect — ArduPilot superset of common.
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink_dialect


def _pymavlink_add_message_is_broken() -> bool:
    """Probe whether pymavlink's add_message crashes on the (no-instance,
    then-instance) sequence. Returns True if it still has the bug we need
    to patch around; False if pymavlink has fixed it upstream.

    The bug: when a message arrives first without an instance-field value
    and is stored via the "simple case" branch, ``._instances`` is not
    initialised. The next call (with an instance value) then tries to
    read ``._instances`` from the stored object and crashes on AttributeError
    or NoneType subscript.
    """

    class _FakeMsg:
        # Mimic enough of a pymavlink message instance for add_message to
        # exercise both branches. _instance_field names the attribute
        # add_message uses to switch between simple / instanced storage.
        _instance_field = "compass_id"

        def __init__(self, compass_id):
            self.compass_id = compass_id

        def get_type(self):
            return "FAKE"

    messages: dict = {}
    try:
        # Step 1: simple-case branch — instance field unset.
        mavutil.add_message(messages, "FAKE", _FakeMsg(compass_id=None))
        # Step 2: instanced branch — bug surfaces when reading _instances
        # from the previously-stored message.
        mavutil.add_message(messages, "FAKE", _FakeMsg(compass_id=1))
    except (AttributeError, TypeError):
        return True
    return False


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


# Only apply the monkey-patch if the installed pymavlink actually still has
# the bug. If upstream has been fixed, leave their implementation alone so
# we don't silently override a corrected version.
if _pymavlink_add_message_is_broken():
    _patch_pymavlink_add_message()

import keelson
from keelson import enclose
from keelson.helpers import (
    enclose_from_float,
    enclose_from_integer,
    enclose_from_string,
)
from keelson.payloads.Decomposed3DVector_pb2 import Decomposed3DVector
from keelson.payloads.EntityHealth_pb2 import (
    CheckResult,
    EntityHealth,
    HealthLevel,
    SourceHealth,
    SubjectHealth,
)
from keelson.payloads.Primitives_pb2 import (
    TimestampedBool,
    TimestampedFloat,
    TimestampedQuaternion,
)
from keelson.interfaces.VehicleNavigation_pb2 import (
    NavigationTarget,
    NavigationTargetAck,
    SetCruiseSpeedRequest,
    SetCruiseSpeedAck,
)
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest,
    ArmAck,
    SetModeRequest,
    SetModeAck,
    EmergencyStopRequest,
    EmergencyStopAck,
    SaveParamsRequest,
    SaveParamsAck,
    RebootRequest,
    RebootAck,
)
from keelson.interfaces.VehicleParam_pb2 import (
    ParamGetRequest,
    ParamSetRequest,
    ParamValueResponse,
    ParamListResponse,
    ParamSetBulkRequest,
    ParamSetBulkResponse,
    ParamSetBulkResult,
)
from keelson.interfaces.VehicleMission_pb2 import (
    Mission,
    MissionItem,
    MissionUploadResponse,
    ClearMissionRequest,
    ClearMissionAck,
    SetCurrentWaypointRequest,
    SetCurrentWaypointAck,
)
from keelson.interfaces.VehicleGeofence_pb2 import (
    Geofence,
    FenceItem,
    GeofenceUploadResponse,
    EnableGeofenceRequest,
    EnableGeofenceAck,
)
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis,
    ManualControlMapping,
    ManualControlMappingAck,
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

# Injection wiring uses skarv as the per-MAVLink-message state machine:
# subscribe-to-vault for companions, fire on the trigger subject, emit one
# MAVLink frame per trigger. injection_config is colocated in bin/ — load
# it via importlib so the connector is still a standalone executable.
import skarv
import skarv.middlewares
from skarv.utilities.zenoh import mirror as skarv_mirror

import importlib.util as _ic_util
import sys as _sys
from importlib.machinery import SourceFileLoader as _ICLoader

# Sibling-module lookup: bin/injection_config.py in the source tree, or
# bin/injection_config (no extension) inside the Docker image, where the
# Dockerfile strips .py from every file copied into /usr/local/bin/.
_bin_dir = Path(__file__).resolve().parent
for _name in ("injection_config.py", "injection_config"):
    _candidate = _bin_dir / _name
    if _candidate.is_file():
        _ic_path = _candidate
        break
else:
    raise ImportError(
        f"Could not locate injection_config helper next to {__file__}; "
        f"checked: {_bin_dir / 'injection_config.py'}, "
        f"{_bin_dir / 'injection_config'}"
    )
if "injection_config" not in _sys.modules:
    _ic_loader = _ICLoader("injection_config", str(_ic_path))
    _ic_spec = _ic_util.spec_from_loader(_ic_loader.name, _ic_loader)
    _ic_mod = _ic_util.module_from_spec(_ic_spec)
    _sys.modules["injection_config"] = _ic_mod
    _ic_spec.loader.exec_module(_ic_mod)
import injection_config  # noqa: E402


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
        "--injection-config",
        type=Path,
        default=None,
        help="Path to a YAML file declaring per-MAVLink-message injection "
        "mappings (see injection_config.py). Without this flag the "
        "connector runs telemetry + commands only; no sensor injection "
        "subscriptions are installed.",
    )
    parser.add_argument(
        "--strict-rates",
        action="store_true",
        help="Raise RuntimeError when an injection-mapping trigger subject's "
        "arrival rate falls below its floor or the producer goes silent. "
        "Forgiving mode (the default) just logs WARN — recommended in "
        "production since a single network hiccup would otherwise kill "
        "the connector. Strict mode is for CI / pre-deploy validation.",
    )
    return parser


# ---------------------------------------------------------------------------
# Downlink: stick-driving via per-axis subscriptions to existing
# joystick_* / wheel_position_pct / lever_position_pct subjects.
#
# Same architectural shape as the GPS_INPUT injection path: each axis is
# a separate subscription to an existing TimestampedFloat subject; samples
# land in a skarv vault under a synthetic per-axis key; an axis arrival
# fires a @skarv.trigger that assembles one MAVLink RC_CHANNELS_OVERRIDE
# from the latest known value of every mapped axis. The connector does
# not invent a new payload type for manual control.
# ---------------------------------------------------------------------------


# Axis names recognized by v1. ArduPilot Rover routes RC input via
# RCMAP_ROLL / RCMAP_THROTTLE; the connector autodetects those channel
# numbers at startup (see _resolve_channels) and stores them on args as
# steering_channel / throttle_channel. The map below ties an axis name to
# the args attribute that holds its resolved channel number.
RECOGNISED_AXES: dict[str, str] = {
    "steering": "steering_channel",
    "throttle": "throttle_channel",
}


def _scale_axis_value(raw_pct: float, *, unipolar: bool, invert: bool) -> float:
    """Map a TimestampedFloat value (in percent) to a unitless [-1, 1]
    deflection for RC override PWM.

    Bipolar (default): raw range [-100, 100] -> [-1.0, 1.0]; raw=0 -> neutral.
    Unipolar (e.g. trigger): raw range [0, 100] -> [0.0, 1.0]; raw=0 -> neutral,
        raw=100 -> full forward. Reverse is unreachable on a unipolar source.
    """
    if unipolar:
        v = max(0.0, min(100.0, raw_pct)) / 100.0
    else:
        v = max(-100.0, min(100.0, raw_pct)) / 100.0
    if invert:
        v = -v
    return v


def _pwm_from_unit(v: float) -> int:
    """[-1, 1] deflection -> RC PWM us, centered 1500 ±500."""
    return int(round(1500 + max(-1.0, min(1.0, v)) * 500))


@dataclass
class _AxisRuntime:
    """Per-axis state held by ManualControlState. The synthetic skarv key
    is the axis name (e.g. "steering"); samples land there from the
    subscriber callback installed by set_mapping()."""

    name: str
    config: ManualControlAxis
    subscriber: Any  # zenoh.Subscriber
    last_value: Optional[float] = None
    last_received_at: float = 0.0


class ManualControlState:
    """Owns the live per-axis subscriber set + emits MAVLink
    RC_CHANNELS_OVERRIDE on each axis arrival.

    No axes are subscribed at startup. The set is installed by the
    VehicleControl.set_manual_control_mapping RPC; calling it again
    atomically replaces the active set. There is no CLI default — the
    operator must explicitly wire the mapping, so the connector boots
    undrivable by default.
    """

    def __init__(
        self,
        session: "zenoh.Session",
        args: argparse.Namespace,
        mav,
    ) -> None:
        self._session = session
        self._args = args
        self._mav = mav
        self._axes: dict[str, _AxisRuntime] = {}
        self._min_interval_s: float = 0.0
        self._max_axis_age_s: float = 0.0
        self._last_emit_at: float = 0.0
        self._lock = threading.Lock()

    def set_mapping(self, mapping: ManualControlMapping) -> None:
        """Replace the active mapping atomically. Validates axis names
        and channel availability; raises ValueError on unknown axes."""
        # Validate up-front so partial-apply isn't possible.
        for axis_name in mapping.axes:
            if axis_name not in RECOGNISED_AXES:
                raise ValueError(
                    f"unknown axis {axis_name!r}; recognised: {sorted(RECOGNISED_AXES)}"
                )
            channel_attr = RECOGNISED_AXES[axis_name]
            if getattr(self._args, channel_attr, None) is None:
                raise ValueError(
                    f"axis {axis_name!r} requires the {channel_attr} arg "
                    f"to be resolved (channel autodetect didn't run?)"
                )

        # Undeclare any current subscribers, then install new ones.
        with self._lock:
            for axis in self._axes.values():
                try:
                    axis.subscriber.undeclare()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to undeclare axis %s subscriber", axis.name
                    )
            self._axes.clear()
            self._min_interval_s = mapping.min_interval_s
            self._max_axis_age_s = mapping.max_axis_age_s
            self._last_emit_at = 0.0

        for axis_name, axis_cfg in mapping.axes.items():
            entity_id = axis_cfg.entity_id or self._args.entity_id
            subject = axis_cfg.subject
            source_id = axis_cfg.source_id or "**"
            if not subject:
                raise ValueError(f"axis {axis_name!r} has empty subject")
            key = keelson.construct_pubsub_key(
                self._args.realm,
                entity_id,
                subject,
                source_id,
            )
            logger.info(
                "manual_control axis %s: subscribing to %s " "(unipolar=%s invert=%s)",
                axis_name,
                key,
                axis_cfg.unipolar,
                axis_cfg.invert,
            )
            normalised = ManualControlAxis(
                entity_id=entity_id,
                subject=subject,
                source_id=source_id,
                unipolar=axis_cfg.unipolar,
                invert=axis_cfg.invert,
            )
            sub = self._session.declare_subscriber(
                key,
                lambda sample, _axis=axis_name: self._on_sample(_axis, sample),
            )
            with self._lock:
                self._axes[axis_name] = _AxisRuntime(
                    name=axis_name,
                    config=normalised,
                    subscriber=sub,
                )

    def get_mapping(self) -> ManualControlMapping:
        with self._lock:
            return ManualControlMapping(
                axes={name: axis.config for name, axis in self._axes.items()},
                min_interval_s=self._min_interval_s,
                max_axis_age_s=self._max_axis_age_s,
            )

    def close(self) -> None:
        self.set_mapping(ManualControlMapping())

    def _on_sample(self, axis_name: str, sample: "zenoh.Sample") -> None:
        try:
            _, _, payload_bytes = keelson.uncover(bytes(sample.payload.to_bytes()))
            msg = TimestampedFloat()
            msg.ParseFromString(payload_bytes)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decode axis %s envelope", axis_name)
            return

        now = time.time()
        with self._lock:
            axis = self._axes.get(axis_name)
            if axis is None:
                return
            axis.last_value = float(msg.value)
            axis.last_received_at = now
            # Throttle gate.
            if (
                self._min_interval_s > 0.0
                and (now - self._last_emit_at) < self._min_interval_s
            ):
                return
            # Staleness check across all axes.
            if self._max_axis_age_s > 0.0:
                for a in self._axes.values():
                    if a.last_received_at == 0.0:
                        # Some axis hasn't arrived yet; skip until it does.
                        return
                    if (now - a.last_received_at) > self._max_axis_age_s:
                        logger.warning(
                            "manual_control: axis %s stale by %.2fs (limit %.2fs); "
                            "skipping emission",
                            a.name,
                            now - a.last_received_at,
                            self._max_axis_age_s,
                        )
                        return
            # Snapshot the channel values while holding the lock so we
            # don't race with a concurrent set_mapping(). MAVLink send
            # happens outside the lock.
            channels = self._compute_channels_locked()
            self._last_emit_at = now

        try:
            self._mav.mav.rc_channels_override_send(
                self._args.target_system,
                0,  # target component (0 = any)
                channels[0],
                channels[1],
                channels[2],
                channels[3],
                channels[4],
                channels[5],
                channels[6],
                channels[7],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send RC_CHANNELS_OVERRIDE")

    def _compute_channels_locked(self) -> list[int]:
        """Build the 8-channel RC override array. Mapped axes contribute
        their scaled-and-PWMed value; unmapped channels stay at 0 (=
        "release the override, use whatever the physical RC has")."""
        channels = [0] * 8
        for axis in self._axes.values():
            if axis.last_value is None:
                continue
            unit = _scale_axis_value(
                axis.last_value,
                unipolar=axis.config.unipolar,
                invert=axis.config.invert,
            )
            channel_attr = RECOGNISED_AXES[axis.name]
            channel = getattr(self._args, channel_attr)
            if 1 <= channel <= 8:
                channels[channel - 1] = _pwm_from_unit(unit)
        return channels


# ---------------------------------------------------------------------------
# Downlink: cmd_arm + cmd_set_mode
# ---------------------------------------------------------------------------


def _send_arm_disarm(mav, target_system: int, target_component: int, arm: bool) -> None:
    """Send a MAV_CMD_COMPONENT_ARM_DISARM via COMMAND_LONG. param2 stays 0
    (no force) — arming-check bypass should be configured on the autopilot
    rather than forced via a kill-switch from the GCS."""
    mav.mav.command_long_send(
        target_system,
        _autopilot_component(target_component),
        mavlink_dialect.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1.0 if arm else 0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )


def _send_set_mode(mav, target_system: int, mode_name: str) -> bool:
    """Look the mode name up in pymavlink's vehicle-aware mapping (populated
    from received HEARTBEATs) and send a SET_MODE. Returns True on send."""
    mode_map = mav.mode_mapping() or {}
    mode_id = mode_map.get(mode_name.upper())
    if mode_id is None:
        logger.error(
            "Unknown mode %r; known modes for this vehicle: %s",
            mode_name,
            sorted(mode_map.keys()),
        )
        return False
    mav.mav.set_mode_send(
        target_system,
        mavlink_dialect.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mode_id,
    )
    return True


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


class _LockingMavProxy:
    """Wraps pymavlink's MAVLink message-builder (``mav.mav``) so concurrent
    threads can call ``*_send`` methods without corrupting pymavlink's
    internal sequence/CRC state.

    The connector sends from at least two threads:
      - the main loop (RPC handlers, injection, channel autodetect),
      - the manual-control axis subscriber callbacks (one per Zenoh
        subscriber, running on Zenoh's IO threads).

    pymavlink does not document concurrent-send safety, so we serialise
    every ``*_send`` call behind one shared lock. Non-send attributes
    (parsers, dialect tables, etc.) pass through unchanged so internal
    pymavlink machinery is unaffected.
    """

    def __init__(self, inner, lock: threading.Lock) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_lock", lock)

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name.endswith("_send") and callable(attr):
            lock = self._lock

            def _locked(*args, **kwargs):
                with lock:
                    return attr(*args, **kwargs)

            return _locked
        return attr

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


def _install_send_lock(mav) -> threading.Lock:
    """Replace ``mav.mav`` with a locking proxy. Returns the lock so callers
    can use it for other shared state if needed."""
    lock = threading.Lock()
    mav.mav = _LockingMavProxy(mav.mav, lock)
    return lock


# ---------------------------------------------------------------------------
# First-run autopilot introspection: read RC/servo mapping from the vehicle,
# cache it under ~/.keelson, re-detect when the autopilot configuration changes.
# ---------------------------------------------------------------------------

# The fingerprint exists to detect operator-visible wiring changes between
# boots (e.g. someone changed RCMAP_ROLL in Mission Planner) and surface
# them via the "fingerprint changed; re-detecting" log line. Only params
# that genuinely affect the steering/throttle channel decision belong here
# — adding more just generates false "wiring changed" alarms when a
# transient PARAM_VALUE drops on a lossy link.
#
# RCMAP_PITCH and RCMAP_YAW are not consulted by the v1 steering/throttle
# logic; SERVO*_FUNCTION wiring is a layer below the RC-channel decision
# this cache records. Both were previously hashed but contributed only
# spurious cache invalidations on flaky serial.
_FINGERPRINT_PARAMS = (
    "FRAME_CLASS",
    "FRAME_TYPE",
    "RCMAP_ROLL",
    "RCMAP_THROTTLE",
)


def _read_params(
    mav,
    target_system: int,
    target_component: int,
    names: Iterable[str],
    timeout: float = 10.0,
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
                    target_system,
                    target_component,
                    name.encode(),
                    -1,
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
    # KEELSON_STATE_DIR lets containers point the channel cache at a
    # mounted volume instead of /root/.keelson (which disappears with the
    # container). Falls back to ~/.keelson for bare-metal use.
    base = os.environ.get("KEELSON_STATE_DIR")
    if base:
        return Path(base) / f"mavlink-{safe}.json"
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
            cli_steering,
            cli_throttle,
        )
        return cli_steering, cli_throttle

    # tlog replay has no live autopilot to answer PARAM_REQUEST_READ; fall
    # back to ArduPilot's stock channel mapping (RC1=steering, RC3=throttle).
    if args.mavlink_url.startswith("tlog:"):
        steering = cli_steering if cli_steering is not None else 1
        throttle = cli_throttle if cli_throttle is not None else 3
        logger.info(
            "tlog replay: skipping autodetect, using defaults steering=RC%d throttle=RC%d",
            steering,
            throttle,
        )
        return steering, throttle

    config_path = args.config_file or _default_config_path(args.entity_id)

    # Always read live params: we need them both to detect (when missing
    # from cache) and to fingerprint (to validate the cache). One round-trip.
    target_component = args.target_component or 1  # ArduPilot autopilot
    logger.info(
        "Reading autopilot params for channel mapping (target=%d/%d)...",
        args.target_system,
        target_component,
    )
    params = _read_params(
        mav,
        args.target_system,
        target_component,
        _FINGERPRINT_PARAMS,
    )
    # The two RCMAP_* params are *required* — without them we can't make a
    # channel decision. The 2 fingerprint companions (FRAME_*) are best-effort:
    # if they drop, the fingerprint just hashes over a smaller set and the
    # next boot may report a benign "wiring changed" log.
    #
    # On lossy serial the first 10 s budget may not be enough for the required
    # params alone; retry just those with a longer extension before giving up.
    required = ("RCMAP_ROLL", "RCMAP_THROTTLE")
    still_missing_required = [p for p in required if p not in params]
    if still_missing_required:
        logger.warning(
            "Required params %s missing after initial read; retrying with "
            "extended timeout",
            still_missing_required,
        )
        extra = _read_params(
            mav,
            args.target_system,
            target_component,
            still_missing_required,
            timeout=20.0,
        )
        params.update(extra)

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
                config_path,
                steering,
                throttle,
            )
            return (
                cli_steering if cli_steering is not None else steering,
                cli_throttle if cli_throttle is not None else throttle,
            )
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
        detected_steering,
        detected_throttle,
        config_path,
    )
    return (
        cli_steering if cli_steering is not None else detected_steering,
        cli_throttle if cli_throttle is not None else detected_throttle,
    )


# ---------------------------------------------------------------------------
# Wire-level MAVLink helpers shared by the RPC handlers below.
# ---------------------------------------------------------------------------


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
        padded[0],
        padded[1],
        padded[2],
        padded[3],
        padded[4],
        padded[5],
        padded[6],
    )


# Per-MAVLink-message (floor, ceiling) injection rates in Hz. ArduPilot's
# EKF expects each injection at a sensor-type-specific cadence; well below
# the floor it will starve / fall back to default sources, well above the
# ceiling we're just wasting MAVLink bandwidth without changing fusion
# outcome. Numbers are heuristics — actual EKF tolerance depends on
# EK3_SRC* weighting — but they're a good default health signal. Watched
# at the trigger subject of each loaded injection mapping.
INJECTION_RATE_LIMITS: dict[str, Tuple[float, float]] = {
    "GPS_INPUT": (5.0, 20.0),
}


class RateMonitor:
    """Observes per-subject arrival rates over a rolling window and reports
    deviations from each subject's (floor, ceiling) band.

    Limits are passed in at construction so callers can key on whatever
    subject they like (typically: the trigger subject of each loaded
    injection mapping, with the band looked up by MAVLink message name in
    INJECTION_RATE_LIMITS).

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

    def __init__(
        self,
        limits: Optional[dict[str, Tuple[float, float]]] = None,
        strict: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._limits: dict[str, Tuple[float, float]] = dict(limits or {})
        self._arrivals: dict[str, deque] = defaultdict(lambda: deque(maxlen=2048))
        self._first_sample_at: dict[str, float] = {}
        # State: "ok" | "below_floor" | "above_ceiling" | "silent"
        self._state: dict[str, str] = {}
        self._strict = strict
        self._last_check_at = 0.0

    def record(self, subject: str) -> None:
        if subject not in self._limits:
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
        """Walk every observed subject and emit warnings / raise if rates
        have crossed a state boundary. Internally rate-limited so callers
        can invoke this every main-loop iteration without overhead.
        """
        now = time.time()
        if now - self._last_check_at < self.CHECK_PERIOD_S:
            return
        self._last_check_at = now

        with self._lock:
            observed_subjects = list(self._first_sample_at.keys())

        for subject in observed_subjects:
            floor, ceiling = self._limits[subject]
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
                    subject,
                    rate,
                    floor,
                    ceiling,
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
                    subject,
                    rate,
                    ceiling,
                )
            elif new_state == "silent":
                msg = (
                    f"{subject} has not produced a sample for {silence:.1f} s "
                    f"after initially streaming — producer dead?"
                )
                if self._strict:
                    raise RuntimeError(msg)
                logger.warning(msg)


# ---------------------------------------------------------------------------
# Sensor injection — file-driven, skarv-buffered.
#
# Each loaded InjectionMapping turns into:
#   1. one skarv_mirror() per configured source subject (zenoh sub -> vault)
#   2. one skarv.trigger() handler bound to the spec's trigger subject
# The trigger reads the latest companions out of the vault, assembles the
# MAVLink frame, and pushes it. Trigger arrivals are recorded with the
# rate monitor; throttle / staleness are applied here (rather than via
# skarv middleware) so the rate-record happens on arrival regardless of
# whether the frame was actually emitted.
# ---------------------------------------------------------------------------


# GPS_INPUT ignore-flag bitmask values (per MAVLink common.xml).
_GPS_IGN_ALT = 1
_GPS_IGN_HDOP = 2
_GPS_IGN_VDOP = 4
_GPS_IGN_VEL_H = 8
_GPS_IGN_VEL_V = 16
_GPS_IGN_SPEED_ACC = 32
_GPS_IGN_HACC = 64
_GPS_IGN_VACC = 128


def _decode_vault_payload(subject: str):
    """Decode the most-recent skarv-vault entry for `subject` into a typed
    protobuf message, or return None if nothing has arrived yet."""
    sample = skarv.get(subject)
    if sample is None:
        return None
    raw = sample.value
    if hasattr(raw, "to_bytes"):
        raw_bytes = bytes(raw.to_bytes())
    else:
        raw_bytes = bytes(raw)
    try:
        _, _, payload_bytes = keelson.uncover(raw_bytes)
        return keelson.decode_protobuf_payload_from_type_name(
            payload_bytes,
            keelson.get_subject_schema(subject),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to decode vault entry for %s", subject)
        return None


def _timestamp_age_seconds(ts, ref_unix_s: float) -> float:
    """Seconds between a google.protobuf.Timestamp and a reference wall
    clock value. Unset Timestamps return +inf so they always count as
    stale (we don't want to forward unstamped companion data)."""
    if not ts.seconds and not ts.nanos:
        return float("inf")
    ts_unix_s = ts.seconds + ts.nanos / 1e9
    return ref_unix_s - ts_unix_s


def _emit_gps_input(
    mav,
    args: argparse.Namespace,
    mapping: "injection_config.InjectionMapping",
) -> bool:
    """Read companions out of the skarv vault, assemble a GPS_INPUT frame
    and push it to MAVLink. Returns True on emission, False if skipped
    (missing trigger, all companions stale, etc.)."""
    fix = _decode_vault_payload(mapping.spec.trigger_subject)
    if fix is None:
        return False

    # Reference time for staleness checks: use the trigger sample's own
    # timestamp if available, else wall clock.
    if fix.timestamp.seconds or fix.timestamp.nanos:
        ref_unix_s = fix.timestamp.seconds + fix.timestamp.nanos / 1e9
    else:
        ref_unix_s = time.time()

    def fetch(subject: str):
        msg = _decode_vault_payload(subject)
        if msg is None:
            return None
        if mapping.max_companion_age_s is not None:
            age = _timestamp_age_seconds(msg.timestamp, ref_unix_s)
            if age > mapping.max_companion_age_s:
                logger.warning(
                    "GPS_INPUT: companion %s stale by %.2fs (limit %.2fs); "
                    "skipping emission",
                    subject,
                    age,
                    mapping.max_companion_age_s,
                )
                raise _CompanionStale()
        return msg

    try:
        fix_type_msg = fetch("gps_fix_type")
        sats_msg = fetch("location_fix_satellites_visible")
        hdop_msg = fetch("location_fix_hdop")
        vdop_msg = fetch("location_fix_vdop")
        hacc_msg = fetch("location_fix_accuracy_horizontal_m")
        vacc_msg = fetch("location_fix_accuracy_vertical_m")
        sog_msg = fetch("speed_over_ground_knots")
        cog_msg = fetch("course_over_ground_deg")
        climb_msg = fetch("climb_rate_mps")
    except _CompanionStale:
        return False

    fix_type = int(fix_type_msg.value) if fix_type_msg is not None else 3  # 3D default
    satellites_visible = int(sats_msg.value) if sats_msg is not None else 6

    ignore = 0
    hdop = hdop_msg.value if hdop_msg is not None else 0.0
    if hdop_msg is None:
        ignore |= _GPS_IGN_HDOP
    vdop = vdop_msg.value if vdop_msg is not None else 0.0
    if vdop_msg is None:
        ignore |= _GPS_IGN_VDOP
    hacc = hacc_msg.value if hacc_msg is not None else 0.0
    if hacc_msg is None:
        ignore |= _GPS_IGN_HACC
    vacc = vacc_msg.value if vacc_msg is not None else 0.0
    if vacc_msg is None:
        ignore |= _GPS_IGN_VACC

    # Velocity: derive vN / vE from SOG (knots) + COG (deg). vD from
    # climb_rate (positive = up; MAVLink convention is positive-down).
    if sog_msg is not None and cog_msg is not None:
        sog_mps = sog_msg.value * 0.514444  # knots -> m/s
        cog_rad = math.radians(cog_msg.value)
        vn = sog_mps * math.cos(cog_rad)
        ve = sog_mps * math.sin(cog_rad)
    else:
        vn = ve = 0.0
        ignore |= _GPS_IGN_VEL_H
    if climb_msg is not None:
        vd = -float(climb_msg.value)
    else:
        vd = 0.0
        ignore |= _GPS_IGN_VEL_V
    ignore |= _GPS_IGN_SPEED_ACC  # no scalar speed-accuracy companion in v1

    # MAVLink time_usec: use the trigger sample's timestamp.
    if fix.timestamp.seconds or fix.timestamp.nanos:
        time_usec = fix.timestamp.seconds * 1_000_000 + fix.timestamp.nanos // 1000
    else:
        time_usec = int(time.time() * 1_000_000)

    mav.mav.gps_input_send(
        time_usec,
        0,  # gps_id (primary)
        ignore,
        0,
        0,  # time_week_ms, time_week (unused)
        fix_type,
        int(fix.latitude * 1e7),
        int(fix.longitude * 1e7),
        float(fix.altitude),
        hdop,
        vdop,
        vn,
        ve,
        vd,
        0.0,  # speed_accuracy (ignored via bit)
        hacc,
        vacc,
        satellites_visible,
    )
    return True


class _CompanionStale(Exception):
    """Signal that a fetched companion is older than max_companion_age_s.
    Caught inside _emit_*, never propagates outside the injection path."""


# Per-MAVLink-message emit registry. Adding a new message means adding a
# MessageSpec to injection_config.MESSAGE_REGISTRY and an emit function
# here.
_INJECTION_EMITTERS: dict[str, Callable[..., bool]] = {
    "GPS_INPUT": _emit_gps_input,
}


class _MappingRuntime:
    """Holds per-mapping state for the trigger callback: last emit time
    (throttle gate) and a reference to the rate monitor. One per loaded
    mapping; bound into the skarv trigger closure."""

    def __init__(
        self,
        mapping: "injection_config.InjectionMapping",
        rate_monitor: "RateMonitor",
    ) -> None:
        self.mapping = mapping
        self.rate_monitor = rate_monitor
        self.last_emit_at: float = 0.0


def _install_injection_mappings(
    session: "zenoh.Session",
    args: argparse.Namespace,
    mav,
    mappings: list["injection_config.InjectionMapping"],
    rate_monitor: "RateMonitor",
) -> None:
    """Wire each loaded mapping into skarv: mirror sources into the vault
    and register the trigger that emits to MAVLink."""
    for mapping in mappings:
        spec = mapping.spec
        emit_fn = _INJECTION_EMITTERS.get(spec.mavlink_message)
        if emit_fn is None:
            raise RuntimeError(
                f"No emitter registered for {spec.mavlink_message} - "
                f"runtime registry out of sync with injection_config.MESSAGE_REGISTRY"
            )

        for src in mapping.sources:
            key = keelson.construct_pubsub_key(
                args.realm,
                src.entity_id,
                src.subject,
                src.source_id,
            )
            logger.info(
                "Injection %s: mirror %s -> skarv[%s]",
                spec.mavlink_message,
                key,
                src.subject,
            )
            skarv_mirror(session, key, src.subject)

        runtime = _MappingRuntime(mapping=mapping, rate_monitor=rate_monitor)

        @skarv.trigger(spec.trigger_subject)
        def _on_trigger(  # noqa: F811  — one closure per mapping
            _runtime=runtime,
            _emit_fn=emit_fn,
            _mav=mav,
            _args=args,
        ):
            _runtime.rate_monitor.record(_runtime.mapping.spec.trigger_subject)
            if _runtime.mapping.throttle_s is not None:
                now = time.time()
                if now - _runtime.last_emit_at < _runtime.mapping.throttle_s:
                    return
            try:
                emitted = _emit_fn(_mav, _args, _runtime.mapping)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Injection emit %s raised — staying alive",
                    _runtime.mapping.spec.mavlink_message,
                )
                return
            if emitted:
                _runtime.last_emit_at = time.time()


# ---------------------------------------------------------------------------
# RPC dispatch — request/response procedures (params, mission, geofence,
# message-interval, command_long escape hatch). Queryable callbacks run on
# Zenoh's IO thread; the actual MAVLink work happens on the main thread via
# a unified rpc_queue.
# ---------------------------------------------------------------------------


class RpcOp(NamedTuple):
    query: Any  # zenoh.Query
    procedure: str
    reply_key: str
    request_bytes: bytes


RPC_PROCEDURES = (
    "get_param",
    "set_param",
    "list_params",
    "set_params",
    "set_message_interval",
    "send_command_long",
    "upload_mission",
    "download_mission",
    "upload_geofence",
    "set_navigation_target",
    "set_cruise_speed",
    "arm",
    "set_mode",
    "emergency_stop",
    "save_params",
    "clear_mission",
    "set_current_waypoint",
    "enable_geofence",
    "reboot",
    "set_manual_control_mapping",
    "get_manual_control_mapping",
)


def _make_rpc_handler(procedure: str, reply_key: str, rpc_queue: "queue.Queue[RpcOp]"):
    def _handler(query) -> None:
        try:
            payload = query.payload
            request_bytes = bytes(payload.to_bytes()) if payload is not None else b""
        except Exception:  # noqa: BLE001
            request_bytes = b""
        try:
            rpc_queue.put_nowait(
                RpcOp(
                    query=query,
                    procedure=procedure,
                    reply_key=reply_key,
                    request_bytes=request_bytes,
                )
            )
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
        key = keelson.construct_rpc_key(
            args.realm, args.entity_id, proc, args.source_id
        )
        q = session.declare_queryable(
            key, _make_rpc_handler(proc, key, rpc_queue), complete=True
        )
        logger.info("Declared RPC queryable: %s", key)
        queryables.append(q)
    return queryables


# ---- RPC handlers --------------------------------------------------------


def _read_params_typed(
    mav,
    target_system: int,
    target_component: int,
    names: Iterable[str],
    timeout: float = 3.0,
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
                    target_system,
                    target_component,
                    name.encode(),
                    -1,
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
        mav,
        args.target_system,
        target_component,
        [req.name],
        timeout=2.0,
    )
    if req.name not in results:
        _reply_err(op.query, f"get_param: no PARAM_VALUE for {req.name!r} within 2s")
        return
    value, ptype = results[req.name]
    op.query.reply(
        op.reply_key,
        ParamValueResponse(
            name=req.name,
            value=value,
            mav_param_type=ptype,
        ).SerializeToString(),
    )


def _handle_set_param(mav, args, op: RpcOp, target_component: int) -> None:
    req = ParamSetRequest()
    req.ParseFromString(op.request_bytes)
    if not req.name:
        _reply_err(op.query, "set_param: 'name' is required")
        return
    mav.mav.param_set_send(
        args.target_system,
        target_component,
        req.name.encode(),
        float(req.value),
        mavlink_dialect.MAV_PARAM_TYPE_REAL32,
    )
    results = _read_params_typed(
        mav,
        args.target_system,
        target_component,
        [req.name],
        timeout=2.0,
    )
    if req.name not in results:
        _reply_err(
            op.query, f"set_param: write of {req.name!r} not confirmed within 2s"
        )
        return
    value, ptype = results[req.name]
    op.query.reply(
        op.reply_key,
        ParamValueResponse(
            name=req.name,
            value=value,
            mav_param_type=ptype,
        ).SerializeToString(),
    )


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
                args.target_system,
                target_component,
                set_req.name.encode(),
                float(set_req.value),
                mavlink_dialect.MAV_PARAM_TYPE_REAL32,
            )
            echoed = _read_params_typed(
                mav,
                args.target_system,
                target_component,
                [set_req.name],
                timeout=2.0,
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
        msg_id = getattr(
            mavlink_dialect, f"MAVLINK_MSG_ID_{req.message_name.upper()}", None
        )
        if msg_id is None:
            _reply_err(
                op.query, f"set_message_interval: unknown message {req.message_name!r}"
            )
            return
    else:
        _reply_err(
            op.query,
            "set_message_interval: either message_id or message_name is required",
        )
        return
    interval_us = -1.0 if req.hz <= 0 else 1_000_000.0 / req.hz
    _send_command_long(
        mav,
        args.target_system,
        target_component,
        mavlink_dialect.MAV_CMD_SET_MESSAGE_INTERVAL,
        float(msg_id),
        float(interval_us),
    )
    ack = mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=2.0)
    op.query.reply(
        op.reply_key,
        SetMessageIntervalResponse(
            accepted=(ack is not None and ack.result == 0),
            mav_result=int(ack.result) if ack is not None else -1,
        ).SerializeToString(),
    )


def _handle_send_command_long(mav, args, op: RpcOp, target_component: int) -> None:
    req = CommandLongRequest()
    req.ParseFromString(op.request_bytes)
    tc = req.target_component if req.HasField("target_component") else target_component
    mav.mav.command_long_send(
        args.target_system,
        _autopilot_component(tc),
        req.command,
        0,
        req.param1,
        req.param2,
        req.param3,
        req.param4,
        req.param5,
        req.param6,
        req.param7,
    )
    ack = mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=3.0)
    op.query.reply(
        op.reply_key,
        CommandLongResponse(
            mav_result=int(ack.result) if ack is not None else -1,
            text="" if ack is not None else "no COMMAND_ACK received within 3s",
        ).SerializeToString(),
    )


# ---- Mission / fence protocol -------------------------------------------


def _missionitem_to_dict(mi: MissionItem) -> dict:
    return {
        "seq": mi.seq,
        "frame": mi.frame,
        "command": mi.command,
        "current": mi.current,
        "autocontinue": mi.autocontinue,
        "param1": mi.param1,
        "param2": mi.param2,
        "param3": mi.param3,
        "param4": mi.param4,
        "x": mi.x,
        "y": mi.y,
        "z": mi.z,
        "mission_type": mi.mission_type,
    }


def _fenceitem_to_dict(fi: FenceItem) -> dict:
    return {
        "seq": fi.seq,
        "frame": fi.frame,
        "command": fi.command,
        "current": False,
        "autocontinue": False,
        "param1": fi.param1,
        "param2": fi.param2,
        "param3": fi.param3,
        "param4": fi.param4,
        "x": fi.x,
        "y": fi.y,
        "z": fi.z,
        "mission_type": 1,  # MAV_MISSION_TYPE_FENCE
    }


def _upload_mission_items(
    mav,
    target_system: int,
    target_component: int,
    items: list[dict],
    mission_type: int,
    timeout: float = 30.0,
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
        return (
            ack.type == 0,
            int(ack.type),
            "" if ack.type == 0 else f"MAV_MISSION_RESULT={ack.type}",
        )

    deadline = time.time() + timeout
    requested: set[int] = set()
    while time.time() < deadline and len(requested) < count:
        msg = mav.recv_match(
            type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
            blocking=True,
            timeout=2.0,
        )
        if msg is None:
            continue
        if msg.get_type() == "MISSION_ACK":
            return (
                msg.type == 0,
                int(msg.type),
                "" if msg.type == 0 else f"MAV_MISSION_RESULT={msg.type}",
            )
        seq = int(msg.seq)
        if seq >= count:
            continue
        item = items[seq]
        mav.mav.mission_item_int_send(
            target_system,
            tc,
            seq,
            int(item["frame"]),
            int(item["command"]),
            1 if item.get("current") else 0,
            1 if item.get("autocontinue") else 0,
            float(item["param1"]),
            float(item["param2"]),
            float(item["param3"]),
            float(item["param4"]),
            int(item["x"]),
            int(item["y"]),
            float(item["z"]),
            mission_type,
        )
        requested.add(seq)
    # Wait for final MISSION_ACK
    ack_deadline = time.time() + 5.0
    while time.time() < ack_deadline:
        msg = mav.recv_match(type="MISSION_ACK", blocking=True, timeout=2.0)
        if msg is not None:
            return (
                msg.type == 0,
                int(msg.type),
                "" if msg.type == 0 else f"MAV_MISSION_RESULT={msg.type}",
            )
    return False, -1, "no MISSION_ACK after full upload"


def _download_mission_items(
    mav,
    target_system: int,
    target_component: int,
    mission_type: int,
    timeout: float = 30.0,
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
        items.append(
            {
                "seq": int(item_msg.seq),
                "frame": int(item_msg.frame),
                "command": int(item_msg.command),
                "current": bool(item_msg.current),
                "autocontinue": bool(item_msg.autocontinue),
                "param1": float(item_msg.param1),
                "param2": float(item_msg.param2),
                "param3": float(item_msg.param3),
                "param4": float(item_msg.param4),
                "x": int(item_msg.x),
                "y": int(item_msg.y),
                "z": float(item_msg.z),
                "mission_type": int(getattr(item_msg, "mission_type", mission_type)),
            }
        )
    mav.mav.mission_ack_send(target_system, tc, 0, mission_type)
    return items


# ---- VehicleNavigation + VehicleLifecycle RPCs --------------------------


def _handle_set_navigation_target(mav, args, op: RpcOp, target_component: int) -> None:
    req = NavigationTarget()
    req.ParseFromString(op.request_bytes)
    if req.latitude == 0.0 and req.longitude == 0.0:
        _reply_err(op.query, "set_navigation_target: latitude/longitude both zero")
        return
    # type_mask: ignore vel(3..5), accel(6..8), force(9), yaw_rate(11). Yaw
    # (bit 10) is conditional — set to ignore only when yaw_deg is omitted.
    type_mask = 0b0000_1010_1111_1000
    if not req.HasField("yaw_deg"):
        type_mask |= 1 << 10  # also ignore yaw
    yaw_rad = math.radians(req.yaw_deg) if req.HasField("yaw_deg") else 0.0
    alt = req.altitude_msl_m if req.HasField("altitude_msl_m") else 0.0
    mav.mav.set_position_target_global_int_send(
        0,  # time_boot_ms
        args.target_system,
        _autopilot_component(target_component),
        mavlink_dialect.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        type_mask,
        int(req.latitude * 1e7),
        int(req.longitude * 1e7),
        alt,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        yaw_rad,
        0.0,
    )
    if req.HasField("ground_speed_mps"):
        _send_command_long(
            mav,
            args.target_system,
            target_component,
            mavlink_dialect.MAV_CMD_DO_CHANGE_SPEED,
            1.0,  # ground speed
            req.ground_speed_mps,
            -1.0,
            0.0,
        )
    op.query.reply(op.reply_key, NavigationTargetAck().SerializeToString())


def _handle_set_cruise_speed(mav, args, op: RpcOp, target_component: int) -> None:
    req = SetCruiseSpeedRequest()
    req.ParseFromString(op.request_bytes)
    _send_command_long(
        mav,
        args.target_system,
        target_component,
        mavlink_dialect.MAV_CMD_DO_CHANGE_SPEED,
        1.0,  # speed_type: ground
        float(req.speed_mps),
        -1.0,  # throttle: -1 = no change
        0.0,
    )
    op.query.reply(op.reply_key, SetCruiseSpeedAck().SerializeToString())


def _handle_arm(mav, args, op: RpcOp, target_component: int) -> None:
    req = ArmRequest()
    req.ParseFromString(op.request_bytes)
    _send_arm_disarm(mav, args.target_system, target_component, bool(req.arm))
    op.query.reply(op.reply_key, ArmAck().SerializeToString())


def _handle_set_mode(mav, args, op: RpcOp, target_component: int) -> None:
    req = SetModeRequest()
    req.ParseFromString(op.request_bytes)
    if not req.mode:
        _reply_err(op.query, "set_mode: mode is empty")
        return
    if not _send_set_mode(mav, args.target_system, req.mode):
        # _send_set_mode logs the known modes; surface the failure mode
        # back to the caller too.
        known = sorted((mav.mode_mapping() or {}).keys())
        _reply_err(
            op.query,
            f"set_mode: unknown mode {req.mode!r}; known: {known}",
        )
        return
    op.query.reply(op.reply_key, SetModeAck().SerializeToString())


def _handle_emergency_stop(mav, args, op: RpcOp, target_component: int) -> None:
    EmergencyStopRequest().ParseFromString(op.request_bytes)
    _send_command_long(
        mav,
        args.target_system,
        target_component,
        mavlink_dialect.MAV_CMD_DO_FLIGHTTERMINATION,
        1.0,
    )
    op.query.reply(op.reply_key, EmergencyStopAck().SerializeToString())


def _handle_save_params(mav, args, op: RpcOp, target_component: int) -> None:
    SaveParamsRequest().ParseFromString(op.request_bytes)
    # MAV_CMD_PREFLIGHT_STORAGE: param1=1 (write params), others -1 (ignore).
    _send_command_long(
        mav,
        args.target_system,
        target_component,
        mavlink_dialect.MAV_CMD_PREFLIGHT_STORAGE,
        1.0,
        -1.0,
        -1.0,
        -1.0,
    )
    op.query.reply(op.reply_key, SaveParamsAck().SerializeToString())


def _handle_clear_mission(mav, args, op: RpcOp, target_component: int) -> None:
    ClearMissionRequest().ParseFromString(op.request_bytes)
    mav.mav.mission_clear_all_send(
        args.target_system,
        _autopilot_component(target_component),
    )
    op.query.reply(op.reply_key, ClearMissionAck().SerializeToString())


def _handle_set_current_waypoint(mav, args, op: RpcOp, target_component: int) -> None:
    req = SetCurrentWaypointRequest()
    req.ParseFromString(op.request_bytes)
    mav.mav.mission_set_current_send(
        args.target_system,
        _autopilot_component(target_component),
        int(req.seq),
    )
    op.query.reply(op.reply_key, SetCurrentWaypointAck().SerializeToString())


def _handle_enable_geofence(mav, args, op: RpcOp, target_component: int) -> None:
    req = EnableGeofenceRequest()
    req.ParseFromString(op.request_bytes)
    _send_command_long(
        mav,
        args.target_system,
        target_component,
        mavlink_dialect.MAV_CMD_DO_FENCE_ENABLE,
        1.0 if req.enabled else 0.0,
    )
    op.query.reply(op.reply_key, EnableGeofenceAck().SerializeToString())


def _handle_reboot(mav, args, op: RpcOp, target_component: int) -> None:
    req = RebootRequest()
    req.ParseFromString(op.request_bytes)
    action_to_p1 = {
        RebootRequest.REBOOT: 1.0,
        RebootRequest.SHUTDOWN: 2.0,
        RebootRequest.REBOOT_TO_BOOTLOADER: 3.0,
    }
    p1 = action_to_p1.get(req.action)
    if p1 is None:
        _reply_err(op.query, f"reboot: action is UNSPECIFIED ({req.action})")
        return
    # param1=autopilot action, param2=companion action (0=do nothing)
    _send_command_long(
        mav,
        args.target_system,
        target_component,
        mavlink_dialect.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
        p1,
        0.0,
    )
    op.query.reply(op.reply_key, RebootAck().SerializeToString())


# ---- VehicleControl: live reconfiguration of manual_control axes ----


def _handle_set_manual_control_mapping(
    mav,
    args,
    op: RpcOp,
    target_component: int,
) -> None:
    req = ManualControlMapping()
    req.ParseFromString(op.request_bytes)
    manual_control_state = args._manual_control_state
    try:
        manual_control_state.set_mapping(req)
    except ValueError as exc:
        _reply_err(op.query, f"set_manual_control_mapping: {exc}")
        return
    op.query.reply(op.reply_key, ManualControlMappingAck().SerializeToString())


def _handle_get_manual_control_mapping(
    mav,
    args,
    op: RpcOp,
    target_component: int,
) -> None:
    manual_control_state = args._manual_control_state
    op.query.reply(
        op.reply_key,
        manual_control_state.get_mapping().SerializeToString(),
    )


# ---- Mission / fence upload + download RPCs -----------------------------


def _handle_upload_mission(mav, args, op: RpcOp, target_component: int) -> None:
    req = Mission()
    req.ParseFromString(op.request_bytes)
    items = [_missionitem_to_dict(mi) for mi in req.items]
    accepted, result, error = _upload_mission_items(
        mav,
        args.target_system,
        target_component,
        items,
        mission_type=0,
    )
    op.query.reply(
        op.reply_key,
        MissionUploadResponse(
            accepted=accepted,
            mission_result=result,
            error=error,
        ).SerializeToString(),
    )


def _handle_download_mission(mav, args, op: RpcOp, target_component: int) -> None:
    items = _download_mission_items(
        mav,
        args.target_system,
        target_component,
        mission_type=0,
    )
    resp = Mission()
    for d in items:
        mi = resp.items.add()
        mi.seq = d["seq"]
        mi.frame = d["frame"]
        mi.command = d["command"]
        mi.current = d["current"]
        mi.autocontinue = d["autocontinue"]
        mi.param1 = d["param1"]
        mi.param2 = d["param2"]
        mi.param3 = d["param3"]
        mi.param4 = d["param4"]
        mi.x = d["x"]
        mi.y = d["y"]
        mi.z = d["z"]
        mi.mission_type = d["mission_type"]
    op.query.reply(op.reply_key, resp.SerializeToString())


def _handle_upload_geofence(mav, args, op: RpcOp, target_component: int) -> None:
    req = Geofence()
    req.ParseFromString(op.request_bytes)
    items = [_fenceitem_to_dict(fi) for fi in req.items]
    accepted, result, error = _upload_mission_items(
        mav,
        args.target_system,
        target_component,
        items,
        mission_type=1,
    )
    op.query.reply(
        op.reply_key,
        GeofenceUploadResponse(
            accepted=accepted,
            mission_result=result,
            error=error,
        ).SerializeToString(),
    )


def _drain_rpc_queue(
    mav,
    args,
    rpc_queue: "queue.Queue[RpcOp]",
    target_component: int,
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
        "set_navigation_target": _handle_set_navigation_target,
        "set_cruise_speed": _handle_set_cruise_speed,
        "arm": _handle_arm,
        "set_mode": _handle_set_mode,
        "emergency_stop": _handle_emergency_stop,
        "save_params": _handle_save_params,
        "clear_mission": _handle_clear_mission,
        "set_current_waypoint": _handle_set_current_waypoint,
        "enable_geofence": _handle_enable_geofence,
        "reboot": _handle_reboot,
        "set_manual_control_mapping": _handle_set_manual_control_mapping,
        "get_manual_control_mapping": _handle_get_manual_control_mapping,
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
    _install_send_lock(mav)

    # Wait for the first HEARTBEAT before reading params — the autopilot may
    # not respond to PARAM_REQUEST_READ while it's still booting.
    logger.info("Waiting for HEARTBEAT before channel auto-detect...")
    hb = mav.wait_heartbeat(timeout=15)
    if hb is None:
        raise RuntimeError("No HEARTBEAT received within 15s")

    args.steering_channel, args.throttle_channel = _resolve_channels(mav, args)

    # Queue for incoming RPC requests, drained on the main thread.
    rpc_queue: "queue.Queue[RpcOp]" = queue.Queue(maxsize=64)

    # Load injection mappings (if any). Per-mapping rate limits feed the
    # rate monitor; missing --injection-config means no injection at all.
    injection_mappings: list["injection_config.InjectionMapping"] = []
    if args.injection_config is not None:
        injection_mappings = injection_config.load_injection_config(
            args.injection_config,
            connector_entity_id=args.entity_id,
            connector_source_id=args.source_id,
        )
        logger.info(
            "Loaded %d injection mapping(s) from %s:\n%s",
            len(injection_mappings),
            args.injection_config,
            injection_config.summarise(injection_mappings),
        )

    rate_limits: dict[str, Tuple[float, float]] = {}
    for m in injection_mappings:
        band = INJECTION_RATE_LIMITS.get(m.spec.mavlink_message)
        if band is not None:
            rate_limits[m.spec.trigger_subject] = band
    rate_monitor = RateMonitor(limits=rate_limits, strict=args.strict_rates)
    if args.strict_rates:
        logger.info(
            "Strict rate monitoring enabled — connector will raise on "
            "injection trigger floor / silence violations"
        )

    logger.info("Opening Zenoh session...")
    with (
        zenoh.open(conf) as session,
        GracefulShutdown() as shutdown,
        declare_liveliness_token(session, args.realm, args.entity_id, args.source_id),
    ):
        # Liveliness here means "the connector is alive and connected to
        # Zenoh", not "the vehicle is alive". Vehicle-alive is observable
        # via the entity_health subject (published per HEARTBEAT) and is
        # the right signal for an aggregator to roll up.
        logger.info("Declared liveliness token (connector alive)")
        # ManualControlState owns the per-axis subscriber set. Empty by
        # default — operators wire it up exclusively via the
        # VehicleControl.set_manual_control_mapping RPC after startup.
        # Attached to args so the RPC handlers can reach it from dispatch.
        manual_control_state = ManualControlState(session, args, mav)
        args._manual_control_state = manual_control_state

        # Injection mappings (file-driven): skarv mirrors + trigger handlers.
        _install_injection_mappings(
            session,
            args,
            mav,
            injection_mappings,
            rate_monitor,
        )

        # RPC queryables — every downlink command path goes through here.
        rpc_queryables = _setup_rpc_queryables(session, args, rpc_queue)

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
                        or msg.get_srcComponent() in (0, args.target_component)
                    ):
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

                # Drain pending RPCs. manual_control axis streams emit
                # MAVLink directly from their @skarv-style subscriber
                # callbacks (see ManualControlState._on_sample); they're
                # not drained here.
                _drain_rpc_queue(
                    mav,
                    args,
                    rpc_queue,
                    args.target_component,
                )
                # Internally rate-limited; safe to call every iteration.
                # In --strict-rates mode this raises and tears down the loop.
                rate_monitor.check()
        finally:
            manual_control_state.close()
            for q in rpc_queryables:
                try:
                    q.undeclare()
                except Exception:  # noqa: BLE001
                    pass
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
