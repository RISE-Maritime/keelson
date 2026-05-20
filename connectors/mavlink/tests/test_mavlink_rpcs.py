"""Unit tests for the two new RPC handlers introduced by the
VehicleNavigation / VehicleLifecycle interfaces.

Both handlers go straight from a typed protobuf request to a MAVLink wire
emission + a typed reply. The tests mock pymavlink and zenoh.Query, drive
the handler directly, and assert on the wire call + reply payload.
"""

import argparse
import time
from unittest.mock import MagicMock

import pytest
from pymavlink.dialects.v20 import ardupilotmega as mavlink_dialect

from conftest import mavlink2keelson

from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.interfaces.VehicleNavigation_pb2 import (
    NavigationTarget,
    NavigationTargetResponse,
    SetCruiseSpeedRequest,
    SetCruiseSpeedResponse,
)
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest,
    ArmResponse,
    SetModeRequest,
    SetModeResponse,
    EmergencyStopRequest,
    EmergencyStopResponse,
    SaveParamsRequest,
    SaveParamsResponse,
    RebootRequest,
    RebootResponse,
)
from keelson.interfaces.VehicleMission_pb2 import (
    ClearMissionRequest,
    ClearMissionResponse,
    Mission,
    SetCurrentWaypointRequest,
    SetCurrentWaypointResponse,
)
from keelson.interfaces.VehicleParam_pb2 import (
    ParamListResponse,
    ParamSetBulkRequest,
    ParamSetBulkResponse,
)
from keelson.interfaces.VehicleGeofence_pb2 import (
    EnableGeofenceRequest,
    EnableGeofenceResponse,
)
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis,
    ManualControlMapping,
    ManualControlMappingAck,
)
from keelson.payloads.Primitives_pb2 import TimestampedFloat as _TF
from keelson import enclose as _enclose
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(target_system=1):
    return argparse.Namespace(
        target_system=target_system,
        target_component=0,
    )


def _make_op(request_msg, procedure: str):
    """Build an RpcOp wrapping a serialized request + a mock query."""
    query = MagicMock()
    query.reply = MagicMock()
    query.reply_err = MagicMock()
    return mavlink2keelson.RpcOp(
        query=query,
        procedure=procedure,
        reply_key="test/@v0/ent/@rpc/" + procedure + "/src",
        request_bytes=request_msg.SerializeToString(),
    )


def _mock_mav():
    """Mock mav connection: `.mav` is the message-builder, and every
    *_send method is a MagicMock so we can capture call args. ``recv_match``
    returns ``None`` by default so handlers that wait for COMMAND_ACK /
    MISSION_ACK get TIMEOUT cleanly; tests that exercise the happy ACK
    path should call ``_program_ack`` to override."""
    mav = MagicMock()
    mav.mav = MagicMock()
    mav.recv_match = MagicMock(return_value=None)
    return mav


def _fake_ack(command: int, result: int = 0):
    """Build a fake COMMAND_ACK with the given command and MAV_RESULT."""
    ack = MagicMock()
    ack.get_type = MagicMock(return_value="COMMAND_ACK")
    ack.command = command
    ack.result = result
    return ack


def _program_ack(mav, *messages):
    """Program ``mav.recv_match`` to return the given messages in order,
    then None on every subsequent call. Each message is returned regardless
    of the ``type=`` filter the handler asks for."""
    iterator = iter(list(messages))

    def _next(*args, **kwargs):
        return next(iterator, None)

    mav.recv_match = MagicMock(side_effect=_next)


def _decoded_err(query: MagicMock) -> str:
    """Decode the ErrorResponse description from a reply_err call."""
    assert query.reply_err.called, "expected reply_err to have been called"
    raw = query.reply_err.call_args.args[0]
    err = ErrorResponse()
    err.ParseFromString(raw)
    return err.error_description


# ---------------------------------------------------------------------------
# set_navigation_target
# ---------------------------------------------------------------------------


class TestSetNavigationTarget:
    def test_minimal_target_emits_set_position_target_global_int(self):
        mav = _mock_mav()
        req = NavigationTarget(latitude=59.351, longitude=18.071)
        op = _make_op(req, "set_navigation_target")

        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        assert mav.mav.set_position_target_global_int_send.called
        call = mav.mav.set_position_target_global_int_send.call_args
        # Positional: time_boot_ms, target_sys, target_comp, frame, type_mask,
        # lat_e7, lon_e7, alt, vx, vy, vz, afx, afy, afz, yaw, yaw_rate
        args = call.args
        assert args[1] == 1  # target_system
        assert args[2] == 1  # autopilot_component(0) -> 1
        assert args[3] == mavlink_dialect.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
        assert args[5] == int(59.351 * 1e7)
        assert args[6] == int(18.071 * 1e7)
        # yaw is ignored when not set (bit 10 in type_mask), so the raw arg
        # value isn't meaningful — just confirm the mask requested yaw-ignore.
        type_mask = args[4]
        assert type_mask & (1 << 10), "yaw-ignore bit not set when yaw_deg omitted"

    def test_optional_yaw_clears_ignore_bit(self):
        mav = _mock_mav()
        req = NavigationTarget(latitude=59.0, longitude=18.0, yaw_deg=90.0)
        op = _make_op(req, "set_navigation_target")

        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        args = mav.mav.set_position_target_global_int_send.call_args.args
        type_mask = args[4]
        assert not (
            type_mask & (1 << 10)
        ), "yaw-ignore bit should be cleared when yaw_deg is provided"

    def test_ground_speed_triggers_change_speed_command(self):
        mav = _mock_mav()
        req = NavigationTarget(latitude=59.0, longitude=18.0, ground_speed_mps=2.5)
        op = _make_op(req, "set_navigation_target")

        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        assert mav.mav.command_long_send.called
        cmd_call = mav.mav.command_long_send.call_args.args
        assert cmd_call[2] == mavlink_dialect.MAV_CMD_DO_CHANGE_SPEED
        # param2 is the speed value
        assert cmd_call[5] == pytest.approx(2.5)

    def test_no_ground_speed_no_extra_command(self):
        mav = _mock_mav()
        req = NavigationTarget(latitude=59.0, longitude=18.0)
        op = _make_op(req, "set_navigation_target")

        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        assert not mav.mav.command_long_send.called

    def test_success_replies_empty_ack(self):
        mav = _mock_mav()
        req = NavigationTarget(latitude=59.0, longitude=18.0)
        op = _make_op(req, "set_navigation_target")

        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        op.query.reply.assert_called_once()
        reply_args = op.query.reply.call_args.args
        assert reply_args[0] == op.reply_key
        # Empty Ack message — parses cleanly with no fields.
        ack = NavigationTargetResponse()
        ack.ParseFromString(reply_args[1])

    def test_zero_lat_zero_lon_returns_error(self):
        mav = _mock_mav()
        req = NavigationTarget(latitude=0.0, longitude=0.0)
        op = _make_op(req, "set_navigation_target")

        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        assert not mav.mav.set_position_target_global_int_send.called
        assert not op.query.reply.called
        assert "latitude/longitude both zero" in _decoded_err(op.query)


# ---------------------------------------------------------------------------
# reboot
# ---------------------------------------------------------------------------


class TestReboot:
    @pytest.mark.parametrize(
        "action, expected_p1",
        [
            (RebootRequest.REBOOT, 1.0),
            (RebootRequest.SHUTDOWN, 2.0),
            (RebootRequest.REBOOT_TO_BOOTLOADER, 3.0),
        ],
    )
    def test_action_maps_to_correct_param1(self, action, expected_p1):
        mav = _mock_mav()
        req = RebootRequest(action=action)
        op = _make_op(req, "reboot")

        mavlink2keelson._handle_reboot(mav, _args(), op, 0)

        assert mav.mav.command_long_send.called
        call = mav.mav.command_long_send.call_args.args
        # Positional: target_sys, target_comp, command, confirmation,
        # param1..param7
        assert call[2] == mavlink_dialect.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN
        assert call[4] == pytest.approx(expected_p1)
        # Companion action is always 0 (we only reboot the autopilot).
        assert call[5] == pytest.approx(0.0)

    def test_success_replies_empty_ack(self):
        mav = _mock_mav()
        req = RebootRequest(action=RebootRequest.REBOOT)
        op = _make_op(req, "reboot")

        mavlink2keelson._handle_reboot(mav, _args(), op, 0)

        op.query.reply.assert_called_once()
        reply_args = op.query.reply.call_args.args
        ack = RebootResponse()
        ack.ParseFromString(reply_args[1])

    def test_unspecified_action_returns_error(self):
        mav = _mock_mav()
        req = RebootRequest(action=RebootRequest.UNSPECIFIED)
        op = _make_op(req, "reboot")

        mavlink2keelson._handle_reboot(mav, _args(), op, 0)

        assert not mav.mav.command_long_send.called
        assert not op.query.reply.called
        assert "UNSPECIFIED" in _decoded_err(op.query)


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------


class TestRpcWiring:
    def test_procedures_include_promoted_rpcs(self):
        # Every cmd_* subject promoted to RPC must appear here.
        for proc in (
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
        ):
            assert proc in mavlink2keelson.RPC_PROCEDURES, proc


# ---------------------------------------------------------------------------
# set_cruise_speed
# ---------------------------------------------------------------------------


class TestSetCruiseSpeed:
    def test_sends_change_speed_command(self):
        mav = _mock_mav()
        req = SetCruiseSpeedRequest(speed_mps=3.5)
        op = _make_op(req, "set_cruise_speed")
        mavlink2keelson._handle_set_cruise_speed(mav, _args(), op, 0)

        assert mav.mav.command_long_send.called
        call = mav.mav.command_long_send.call_args.args
        assert call[2] == mavlink_dialect.MAV_CMD_DO_CHANGE_SPEED
        # param1=1 (ground speed), param2=speed_mps, param3=-1 (no throttle change)
        assert call[4] == pytest.approx(1.0)
        assert call[5] == pytest.approx(3.5)
        assert call[6] == pytest.approx(-1.0)

        op.query.reply.assert_called_once()
        SetCruiseSpeedResponse().ParseFromString(op.query.reply.call_args.args[1])


# ---------------------------------------------------------------------------
# arm + set_mode
# ---------------------------------------------------------------------------


class TestArm:
    @pytest.mark.parametrize("arm,expected_p1", [(True, 1.0), (False, 0.0)])
    def test_arm_disarm_maps_to_command_long(self, arm, expected_p1):
        mav = _mock_mav()
        req = ArmRequest(arm=arm)
        op = _make_op(req, "arm")
        mavlink2keelson._handle_arm(mav, _args(), op, 0)

        assert mav.mav.command_long_send.called
        call = mav.mav.command_long_send.call_args.args
        assert call[2] == mavlink_dialect.MAV_CMD_COMPONENT_ARM_DISARM
        assert call[4] == pytest.approx(expected_p1)

        op.query.reply.assert_called_once()
        ArmResponse().ParseFromString(op.query.reply.call_args.args[1])


class TestSetMode:
    def _mav_with_modes(self, modes: dict[str, int]):
        mav = _mock_mav()
        mav.mode_mapping = MagicMock(return_value=modes)
        return mav

    def test_known_mode_sends_do_set_mode(self):
        # set_mode now goes via MAV_CMD_DO_SET_MODE (COMMAND_LONG) rather
        # than the legacy SET_MODE message -- the COMMAND_LONG path is
        # acked by the autopilot, the SET_MODE one isn't.
        mav = self._mav_with_modes({"MANUAL": 0, "GUIDED": 15})
        req = SetModeRequest(mode="GUIDED")
        op = _make_op(req, "set_mode")
        mavlink2keelson._handle_set_mode(mav, _args(), op, 0)

        assert not mav.mav.set_mode_send.called
        assert mav.mav.command_long_send.called
        call = mav.mav.command_long_send.call_args.args
        # Positional: target_sys, target_comp, command, confirmation,
        # param1..param7. command should be DO_SET_MODE, param1 carries the
        # CUSTOM_MODE_ENABLED base-mode flag, param2 the custom mode id.
        assert call[2] == mavlink_dialect.MAV_CMD_DO_SET_MODE
        assert call[4] == pytest.approx(
            float(mavlink_dialect.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED)
        )
        assert call[5] == pytest.approx(15.0)
        op.query.reply.assert_called_once()
        SetModeResponse().ParseFromString(op.query.reply.call_args.args[1])

    def test_unknown_mode_returns_error(self):
        mav = self._mav_with_modes({"MANUAL": 0})
        req = SetModeRequest(mode="WARP_SPEED")
        op = _make_op(req, "set_mode")
        mavlink2keelson._handle_set_mode(mav, _args(), op, 0)

        assert not mav.mav.command_long_send.called
        assert not op.query.reply.called
        err = _decoded_err(op.query)
        assert "WARP_SPEED" in err
        assert "MANUAL" in err  # listed alongside known modes

    def test_empty_mode_returns_error(self):
        mav = _mock_mav()
        req = SetModeRequest(mode="")
        op = _make_op(req, "set_mode")
        mavlink2keelson._handle_set_mode(mav, _args(), op, 0)
        assert "empty" in _decoded_err(op.query)


# ---------------------------------------------------------------------------
# emergency_stop, save_params
# ---------------------------------------------------------------------------


class TestEmergencyStop:
    def test_sends_flight_termination(self):
        mav = _mock_mav()
        op = _make_op(EmergencyStopRequest(), "emergency_stop")
        mavlink2keelson._handle_emergency_stop(mav, _args(), op, 0)

        call = mav.mav.command_long_send.call_args.args
        assert call[2] == mavlink_dialect.MAV_CMD_DO_FLIGHTTERMINATION
        assert call[4] == pytest.approx(1.0)
        op.query.reply.assert_called_once()
        EmergencyStopResponse().ParseFromString(op.query.reply.call_args.args[1])


class TestSaveParams:
    def test_sends_preflight_storage(self):
        mav = _mock_mav()
        op = _make_op(SaveParamsRequest(), "save_params")
        mavlink2keelson._handle_save_params(mav, _args(), op, 0)

        call = mav.mav.command_long_send.call_args.args
        assert call[2] == mavlink_dialect.MAV_CMD_PREFLIGHT_STORAGE
        assert call[4] == pytest.approx(1.0)  # write
        assert call[5] == pytest.approx(-1.0)  # ignore other slots
        op.query.reply.assert_called_once()
        SaveParamsResponse().ParseFromString(op.query.reply.call_args.args[1])


# ---------------------------------------------------------------------------
# clear_mission, set_current_waypoint, enable_geofence
# ---------------------------------------------------------------------------


class TestClearMission:
    def test_sends_mission_clear_all(self):
        mav = _mock_mav()
        op = _make_op(ClearMissionRequest(), "clear_mission")
        mavlink2keelson._handle_clear_mission(mav, _args(), op, 0)
        assert mav.mav.mission_clear_all_send.called
        op.query.reply.assert_called_once()
        ClearMissionResponse().ParseFromString(op.query.reply.call_args.args[1])


class TestSetCurrentWaypoint:
    def test_sends_mission_set_current(self):
        mav = _mock_mav()
        op = _make_op(SetCurrentWaypointRequest(seq=4), "set_current_waypoint")
        mavlink2keelson._handle_set_current_waypoint(mav, _args(), op, 0)
        call = mav.mav.mission_set_current_send.call_args.args
        # (target_sys, target_comp, seq)
        assert call[2] == 4
        op.query.reply.assert_called_once()
        SetCurrentWaypointResponse().ParseFromString(op.query.reply.call_args.args[1])


class TestEnableGeofence:
    @pytest.mark.parametrize("enabled,expected", [(True, 1.0), (False, 0.0)])
    def test_param1_reflects_enabled(self, enabled, expected):
        mav = _mock_mav()
        op = _make_op(EnableGeofenceRequest(enabled=enabled), "enable_geofence")
        mavlink2keelson._handle_enable_geofence(mav, _args(), op, 0)
        call = mav.mav.command_long_send.call_args.args
        assert call[2] == mavlink_dialect.MAV_CMD_DO_FENCE_ENABLE
        assert call[4] == pytest.approx(expected)
        op.query.reply.assert_called_once()
        EnableGeofenceResponse().ParseFromString(op.query.reply.call_args.args[1])


# ---------------------------------------------------------------------------
# VehicleControl: manual_control axis-mapping runtime + RPCs
# ---------------------------------------------------------------------------


def _wrap_zenoh_bytes(payload_bytes: bytes):
    """Wrap raw bytes in a mock zenoh payload with .to_bytes() — matches
    the shape the subscriber callback expects (sample.payload.to_bytes())."""
    p = MagicMock()
    p.to_bytes = MagicMock(return_value=payload_bytes)
    return p


def _mock_sample(value_pct: float):
    sample = MagicMock()
    tf = _TF(value=value_pct)
    tf.timestamp.GetCurrentTime()
    sample.payload = _wrap_zenoh_bytes(_enclose(tf.SerializeToString()))
    return sample


class TestManualControlAxisState:
    def _make_state(
        self,
        entity_id="motorboat-01",
        steering_channel=1,
        throttle_channel=3,
        target_system=1,
    ):
        session = MagicMock()
        session.declare_subscriber = MagicMock(side_effect=lambda key, cb: MagicMock())
        args = argparse.Namespace(
            realm="rise",
            entity_id=entity_id,
            steering_channel=steering_channel,
            throttle_channel=throttle_channel,
            target_system=target_system,
        )
        mav = _mock_mav()
        state = mavlink2keelson.ManualControlState(session, args, mav)
        return state, session, mav

    def test_starts_with_no_subscribers(self):
        state, session, _ = self._make_state()
        assert state.get_mapping().axes == {}
        assert not session.declare_subscriber.called

    def test_set_mapping_declares_per_axis_subscribers(self):
        state, session, _ = self._make_state()
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct", source_id="js-1"
                    ),
                    "throttle": ManualControlAxis(
                        subject="joystick_y_pct", source_id="js-1"
                    ),
                }
            )
        )
        assert session.declare_subscriber.call_count == 2
        keys = [c.args[0] for c in session.declare_subscriber.call_args_list]
        assert any(k.endswith("joystick_x_pct/js-1") for k in keys)
        assert any(k.endswith("joystick_y_pct/js-1") for k in keys)

    def test_set_mapping_replaces_old_subscribers(self):
        state, session, _ = self._make_state()
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct", source_id="js-1"
                    ),
                }
            )
        )
        # Reconfigure with a different source_id.
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct", source_id="js-2"
                    ),
                }
            )
        )
        # Two declares total (one per set_mapping call).
        assert session.declare_subscriber.call_count == 2

    def test_unknown_axis_name_raises(self):
        state, session, _ = self._make_state()
        with pytest.raises(ValueError, match="unknown axis"):
            state.set_mapping(
                ManualControlMapping(
                    axes={
                        "ailerons": ManualControlAxis(
                            subject="joystick_x_pct", source_id="js-1"
                        ),
                    }
                )
            )
        # No partial-apply.
        assert state.get_mapping().axes == {}
        assert not session.declare_subscriber.called

    def test_missing_channel_raises(self):
        state, session, _ = self._make_state(steering_channel=None)
        with pytest.raises(ValueError, match="steering_channel"):
            state.set_mapping(
                ManualControlMapping(
                    axes={
                        "steering": ManualControlAxis(
                            subject="joystick_x_pct", source_id="js-1"
                        ),
                    }
                )
            )

    def test_empty_axes_undeclares_all(self):
        state, session, _ = self._make_state()
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct", source_id="js-1"
                    ),
                }
            )
        )
        state.set_mapping(ManualControlMapping())
        assert state.get_mapping().axes == {}

    def test_arrival_emits_rc_override(self):
        state, session, mav = self._make_state(
            steering_channel=1,
            throttle_channel=3,
        )
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct", source_id="js-1"
                    ),
                    "throttle": ManualControlAxis(
                        subject="joystick_y_pct", source_id="js-1"
                    ),
                }
            )
        )
        # Pull the on_sample callbacks the subscriber recorded for each axis.
        callbacks = {
            c.args[0].split("/")[-2]: c.args[1]  # subject is second-to-last segment
            for c in session.declare_subscriber.call_args_list
        }
        # Fire steering and throttle.
        callbacks["joystick_x_pct"](_mock_sample(100.0))  # full right
        callbacks["joystick_y_pct"](_mock_sample(50.0))  # half forward

        # Each arrival emits one RC_CHANNELS_OVERRIDE.
        assert mav.mav.rc_channels_override_send.call_count == 2
        last_call = mav.mav.rc_channels_override_send.call_args.args
        # Positional: target_sys, target_comp, c1..c8
        # steering on channel 1 = 100% -> PWM 2000
        assert last_call[2] == 2000
        # throttle on channel 3 = 50% -> PWM 1750
        assert last_call[4] == 1750
        # Unmapped channels stay at 0 ("release override").
        assert last_call[3] == 0
        assert last_call[5] == 0

    def test_unipolar_scaling(self):
        state, session, mav = self._make_state(
            steering_channel=1,
            throttle_channel=3,
        )
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "throttle": ManualControlAxis(
                        subject="joystick_rt_pct",
                        source_id="js-1",
                        unipolar=True,
                    ),
                }
            )
        )
        cb = session.declare_subscriber.call_args_list[0].args[1]
        # Unipolar: 0 raw -> 0.0 unit -> PWM 1500 (neutral).
        cb(_mock_sample(0.0))
        assert mav.mav.rc_channels_override_send.call_args.args[4] == 1500
        # Unipolar: 100 raw -> 1.0 unit -> PWM 2000 (full forward).
        cb(_mock_sample(100.0))
        assert mav.mav.rc_channels_override_send.call_args.args[4] == 2000

    def test_invert_flag_flips_sign(self):
        state, session, mav = self._make_state(
            steering_channel=1,
            throttle_channel=3,
        )
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "throttle": ManualControlAxis(
                        subject="joystick_y_pct",
                        source_id="js-1",
                        invert=True,
                    ),
                }
            )
        )
        cb = session.declare_subscriber.call_args_list[0].args[1]
        # +50% bipolar -> +0.5 unit -> invert -> -0.5 -> PWM 1250.
        cb(_mock_sample(50.0))
        assert mav.mav.rc_channels_override_send.call_args.args[4] == 1250

    def test_throttle_gate_skips_close_emissions(self):
        state, session, mav = self._make_state()
        state.set_mapping(
            ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct", source_id="js-1"
                    ),
                },
                min_interval_s=10.0,
            )
        )
        cb = session.declare_subscriber.call_args_list[0].args[1]
        cb(_mock_sample(50.0))
        cb(_mock_sample(60.0))  # within throttle window -> dropped
        assert mav.mav.rc_channels_override_send.call_count == 1


class TestManualControlMappingRpcs:
    def test_set_mapping_calls_state(self):
        state = MagicMock()
        args = argparse.Namespace(_manual_control_state=state)
        req = ManualControlMapping(
            axes={
                "steering": ManualControlAxis(
                    subject="joystick_x_pct", source_id="js-1"
                ),
            }
        )
        op = _make_op(req, "set_manual_control_mapping")
        mavlink2keelson._handle_set_manual_control_mapping(MagicMock(), args, op, 0)

        state.set_mapping.assert_called_once()
        passed = state.set_mapping.call_args.args[0]
        assert "steering" in passed.axes
        op.query.reply.assert_called_once()
        ManualControlMappingAck().ParseFromString(op.query.reply.call_args.args[1])

    def test_set_mapping_value_error_returns_error_response(self):
        state = MagicMock()
        state.set_mapping.side_effect = ValueError("unknown axis 'ailerons'")
        args = argparse.Namespace(_manual_control_state=state)
        op = _make_op(ManualControlMapping(), "set_manual_control_mapping")
        mavlink2keelson._handle_set_manual_control_mapping(MagicMock(), args, op, 0)

        assert not op.query.reply.called
        err = _decoded_err(op.query)
        assert "ailerons" in err

    def test_get_mapping_returns_state(self):
        state = MagicMock()
        state.get_mapping.return_value = ManualControlMapping(
            axes={
                "steering": ManualControlAxis(
                    entity_id="motorboat-01",
                    subject="joystick_x_pct",
                    source_id="js-1",
                ),
            }
        )
        args = argparse.Namespace(_manual_control_state=state)
        op = _make_op(ManualControlMapping(), "get_manual_control_mapping")
        mavlink2keelson._handle_get_manual_control_mapping(MagicMock(), args, op, 0)

        op.query.reply.assert_called_once()
        resp = ManualControlMapping()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert "steering" in resp.axes
        assert resp.axes["steering"].subject == "joystick_x_pct"


# ---------------------------------------------------------------------------
# Helpers for MAVLink protocol tests
# ---------------------------------------------------------------------------


def _scripted_recv(messages):
    """Build a recv_match side_effect that yields each message in order
    then returns None forever. Filters by the `type` kwarg so callers
    can interleave multiple message types into one script."""
    queue_by_type: dict[str, list] = {}
    catchall: list = []
    for msg in messages:
        mtype = (
            msg.get_type() if hasattr(msg, "get_type") else getattr(msg, "_type", None)
        )
        if mtype is None:
            catchall.append(msg)
        else:
            queue_by_type.setdefault(mtype, []).append(msg)

    def _recv(type=None, blocking=False, timeout=0):
        # type may be a list (e.g. mission protocol passes a list of accepted
        # types) or a single string.
        if type is None:
            if catchall:
                return catchall.pop(0)
            return None
        wanted = [type] if isinstance(type, str) else list(type)
        for t in wanted:
            if queue_by_type.get(t):
                return queue_by_type[t].pop(0)
        return None

    return _recv


class _FakeMavMsg:
    """Small stand-in for a parsed pymavlink message. Avoids constructing
    real dialect messages (with all their required fields) when the
    handler only reads a handful of attributes."""

    def __init__(self, msg_type: str, **fields):
        self._type = msg_type
        for k, v in fields.items():
            setattr(self, k, v)

    def get_type(self):
        return self._type


# ---------------------------------------------------------------------------
# download_mission
# ---------------------------------------------------------------------------


class TestDownloadMission:
    def test_happy_path_round_trips_items(self):
        # SITL responds with MISSION_COUNT then one MISSION_ITEM_INT per seq.
        mav = _mock_mav()
        items = [
            _FakeMavMsg(
                "MISSION_ITEM_INT",
                seq=i,
                frame=3,
                command=16,
                current=0,
                autocontinue=1,
                param1=0.0,
                param2=0.0,
                param3=0.0,
                param4=0.0,
                x=575780000 + i,
                y=119500000 + i,
                z=10.0,
                mission_type=0,
            )
            for i in range(3)
        ]
        mav.recv_match = MagicMock(
            side_effect=_scripted_recv([_FakeMavMsg("MISSION_COUNT", count=3), *items])
        )
        op = _make_op(Mission(), "download_mission")
        mavlink2keelson._handle_download_mission(mav, _args(), op, 0)

        op.query.reply.assert_called_once()
        resp = Mission()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert len(resp.items) == 3
        assert resp.items[0].x == 575780000
        assert resp.items[2].x == 575780002
        # mission_request_list was sent up front, plus one request per item.
        assert mav.mav.mission_request_list_send.called
        assert mav.mav.mission_request_int_send.call_count == 3
        # ACK closes the protocol exchange.
        assert mav.mav.mission_ack_send.called

    def test_empty_mission_returns_empty(self):
        mav = _mock_mav()
        mav.recv_match = MagicMock(
            side_effect=_scripted_recv([_FakeMavMsg("MISSION_COUNT", count=0)])
        )
        op = _make_op(Mission(), "download_mission")
        mavlink2keelson._handle_download_mission(mav, _args(), op, 0)

        resp = Mission()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert len(resp.items) == 0
        assert not mav.mav.mission_request_int_send.called

    def test_no_mission_count_returns_empty(self):
        # Autopilot never answered MISSION_REQUEST_LIST. Handler must not
        # block forever — _download_mission_items returns [] after the
        # 3-second MISSION_COUNT timeout.
        mav = _mock_mav()
        mav.recv_match = MagicMock(return_value=None)
        op = _make_op(Mission(), "download_mission")
        mavlink2keelson._handle_download_mission(mav, _args(), op, 0)

        resp = Mission()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert len(resp.items) == 0
        # Even with no items we still send the request_list.
        assert mav.mav.mission_request_list_send.called

    def test_item_timeout_returns_partial(self):
        # MISSION_COUNT said 3 items, but the autopilot only delivers seq=0
        # before going silent. Handler stops at the timeout with the items
        # collected so far.
        mav = _mock_mav()
        item0 = _FakeMavMsg(
            "MISSION_ITEM_INT",
            seq=0,
            frame=3,
            command=16,
            current=0,
            autocontinue=1,
            param1=0.0,
            param2=0.0,
            param3=0.0,
            param4=0.0,
            x=1,
            y=2,
            z=3.0,
            mission_type=0,
        )

        def _recv(type=None, blocking=False, timeout=0):
            wanted = [type] if isinstance(type, str) else list(type or [])
            if "MISSION_COUNT" in wanted:
                return _FakeMavMsg("MISSION_COUNT", count=3)
            if "MISSION_ITEM_INT" in wanted:
                # Hand out seq=0 once, then stall.
                if not getattr(_recv, "_handed_out", False):
                    _recv._handed_out = True
                    return item0
                return None
            return None

        mav.recv_match = MagicMock(side_effect=_recv)
        op = _make_op(Mission(), "download_mission")
        mavlink2keelson._handle_download_mission(mav, _args(), op, 0)

        resp = Mission()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert len(resp.items) == 1
        assert resp.items[0].x == 1


# ---------------------------------------------------------------------------
# list_params
# ---------------------------------------------------------------------------


class TestListParams:
    def _param_msg(self, name: str, value: float, ptype: int = 9, count: int = 2):
        # ptype 9 = MAV_PARAM_TYPE_REAL32 in MAVLink common.
        return _FakeMavMsg(
            "PARAM_VALUE",
            param_id=name.encode().ljust(16, b"\x00"),
            param_value=value,
            param_type=ptype,
            param_count=count,
            param_index=0,
        )

    def test_happy_path_collects_all_params(self):
        mav = _mock_mav()
        mav.recv_match = MagicMock(
            side_effect=_scripted_recv(
                [
                    self._param_msg("RCMAP_ROLL", 1.0, count=2),
                    self._param_msg("RCMAP_THROTTLE", 3.0, count=2),
                ]
            )
        )
        op = _make_op(Mission(), "list_params")  # request body is ignored
        mavlink2keelson._handle_list_params(mav, _args(), op, 0)

        assert mav.mav.param_request_list_send.called
        op.query.reply.assert_called_once()
        resp = ParamListResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        # Sorted alphabetically per the handler.
        names = [p.name for p in resp.params]
        assert names == ["RCMAP_ROLL", "RCMAP_THROTTLE"]
        assert resp.params[0].value == pytest.approx(1.0)
        assert resp.params[1].value == pytest.approx(3.0)

    def test_no_params_replies_empty(self):
        # Autopilot never responds — handler still replies with an empty
        # list rather than blocking forever. We can't easily simulate the
        # 30s deadline; instead verify the no-response path by patching
        # time.time so the deadline is already past.
        mav = _mock_mav()
        mav.recv_match = MagicMock(return_value=None)

        # Monkey-patch time.time inside the module to skip the 30s wait.
        real_time = time.time
        t = [real_time()]

        def fake_time():
            t[0] += 31.0
            return t[0]

        original = mavlink2keelson.time.time
        mavlink2keelson.time.time = fake_time
        try:
            op = _make_op(Mission(), "list_params")
            mavlink2keelson._handle_list_params(mav, _args(), op, 0)
        finally:
            mavlink2keelson.time.time = original

        op.query.reply.assert_called_once()
        resp = ParamListResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert len(resp.params) == 0


# ---------------------------------------------------------------------------
# set_params (bulk)
# ---------------------------------------------------------------------------


class TestSetParams:
    def test_mixed_success_and_unconfirmed_writes(self, monkeypatch):
        # Two params requested; the autopilot echoes back the first but
        # never the second. Result list should have ok=True for the first
        # and ok=False (error: "write not confirmed") for the second.
        # Stub _read_params_typed to bypass the 2-second per-param timeout
        # — we're testing the handler's per-result bookkeeping, not the
        # MAVLink read protocol (which has its own tests).
        mav = _mock_mav()

        confirmed_name = "RCMAP_ROLL"

        def fake_read_params_typed(_mav, _tsys, _tcomp, names, timeout=2.0):
            return {name: (2.0, 9) for name in names if name == confirmed_name}

        monkeypatch.setattr(
            mavlink2keelson, "_read_params_typed", fake_read_params_typed
        )

        req = ParamSetBulkRequest()
        a = req.params.add()
        a.name = confirmed_name
        a.value = 2.0
        b = req.params.add()
        b.name = "RCMAP_NEVER"
        b.value = 5.0

        op = _make_op(req, "set_params")
        mavlink2keelson._handle_set_params(mav, _args(), op, 0)

        # Both writes were attempted on the wire.
        assert mav.mav.param_set_send.call_count == 2

        op.query.reply.assert_called_once()
        resp = ParamSetBulkResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        by_name = {r.name: r for r in resp.results}
        assert by_name[confirmed_name].ok is True
        assert by_name[confirmed_name].value == pytest.approx(2.0)
        assert by_name["RCMAP_NEVER"].ok is False
        assert "not confirmed" in by_name["RCMAP_NEVER"].error

    def test_all_ok_when_every_write_confirmed(self, monkeypatch):
        mav = _mock_mav()
        monkeypatch.setattr(
            mavlink2keelson,
            "_read_params_typed",
            lambda *a, **kw: {name: (1.0, 9) for name in a[3]},
        )
        req = ParamSetBulkRequest()
        for name in ("FOO", "BAR"):
            p = req.params.add()
            p.name = name
            p.value = 1.0
        op = _make_op(req, "set_params")
        mavlink2keelson._handle_set_params(mav, _args(), op, 0)

        resp = ParamSetBulkResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert all(r.ok for r in resp.results)
        assert {r.name for r in resp.results} == {"FOO", "BAR"}


# ---------------------------------------------------------------------------
# CommandResult mapping + new ACK-aware handler paths
# ---------------------------------------------------------------------------


class TestCommandResultMapping:
    def test_mav_result_known_codes(self):
        assert (
            mavlink2keelson._command_result_from_mav_result(0)
            == CommandResult.COMMAND_RESULT_ACCEPTED
        )
        assert (
            mavlink2keelson._command_result_from_mav_result(1)
            == CommandResult.COMMAND_RESULT_TEMPORARILY_REJECTED
        )
        assert (
            mavlink2keelson._command_result_from_mav_result(3)
            == CommandResult.COMMAND_RESULT_UNSUPPORTED
        )
        assert (
            mavlink2keelson._command_result_from_mav_result(6)
            == CommandResult.COMMAND_RESULT_CANCELLED
        )

    def test_mav_result_unknown_falls_through_to_failed(self):
        # COMMAND_INT_ONLY (7), COMMAND_UNSUPPORTED_MAV_FRAME (8), or any
        # forward-compat code we haven't modeled -> FAILED with the raw
        # code preserved by the caller.
        assert (
            mavlink2keelson._command_result_from_mav_result(7)
            == CommandResult.COMMAND_RESULT_FAILED
        )
        assert (
            mavlink2keelson._command_result_from_mav_result(99)
            == CommandResult.COMMAND_RESULT_FAILED
        )

    def test_mission_result_known_codes(self):
        assert (
            mavlink2keelson._command_result_from_mission_result(0)
            == CommandResult.COMMAND_RESULT_ACCEPTED
        )
        # NO_SPACE -> FAILED (we don't model "no space" specially)
        assert (
            mavlink2keelson._command_result_from_mission_result(4)
            == CommandResult.COMMAND_RESULT_FAILED
        )
        # INVALID_SEQUENCE -> DENIED
        assert (
            mavlink2keelson._command_result_from_mission_result(12)
            == CommandResult.COMMAND_RESULT_DENIED
        )
        # OPERATION_CANCELLED -> FAILED, CANCELLED -> CANCELLED
        assert (
            mavlink2keelson._command_result_from_mission_result(14)
            == CommandResult.COMMAND_RESULT_FAILED
        )
        assert (
            mavlink2keelson._command_result_from_mission_result(15)
            == CommandResult.COMMAND_RESULT_CANCELLED
        )


class TestWaitCommandAck:
    def test_matching_ack_returns_normalized_result(self):
        mav = _mock_mav()
        _program_ack(mav, _fake_ack(mavlink_dialect.MAV_CMD_DO_SET_MODE, result=0))
        result, raw, detail = mavlink2keelson._wait_command_ack(
            mav, mavlink_dialect.MAV_CMD_DO_SET_MODE, timeout=0.1
        )
        assert result == CommandResult.COMMAND_RESULT_ACCEPTED
        assert raw == 0
        assert detail == ""

    def test_unmatching_ack_is_skipped_then_timeout(self):
        # An ACK for a *different* command should not satisfy the wait.
        mav = _mock_mav()
        _program_ack(mav, _fake_ack(999, result=0))
        result, raw, detail = mavlink2keelson._wait_command_ack(
            mav, mavlink_dialect.MAV_CMD_DO_SET_MODE, timeout=0.05
        )
        assert result == CommandResult.COMMAND_RESULT_TIMEOUT
        assert raw == -1
        assert "no COMMAND_ACK" in detail

    def test_denied_result_is_normalized(self):
        mav = _mock_mav()
        _program_ack(mav, _fake_ack(mavlink_dialect.MAV_CMD_DO_FENCE_ENABLE, result=2))
        result, raw, _ = mavlink2keelson._wait_command_ack(
            mav, mavlink_dialect.MAV_CMD_DO_FENCE_ENABLE, timeout=0.1
        )
        assert result == CommandResult.COMMAND_RESULT_DENIED
        assert raw == 2


class TestHandlerHappyPaths:
    """Spot-checks of the ACCEPTED path for handlers whose new shape
    depends on translating COMMAND_ACK into CommandResult. The smoke
    tests above only confirm the wire emission; these confirm the
    response payload."""

    def test_arm_accepted(self):
        mav = _mock_mav()
        _program_ack(
            mav, _fake_ack(mavlink_dialect.MAV_CMD_COMPONENT_ARM_DISARM, result=0)
        )
        op = _make_op(ArmRequest(arm=True), "arm")
        mavlink2keelson._handle_arm(mav, _args(), op, 0)

        resp = ArmResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
        assert resp.raw_autopilot_result == 0

    def test_arm_timeout_when_no_ack(self):
        mav = _mock_mav()  # recv_match returns None by default
        op = _make_op(ArmRequest(arm=True), "arm")
        mavlink2keelson._handle_arm(mav, _args(), op, 0)

        resp = ArmResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert resp.result == CommandResult.COMMAND_RESULT_TIMEOUT
        assert resp.raw_autopilot_result == -1
        assert "no COMMAND_ACK" in resp.detail

    def test_set_mode_populates_mode_actual_from_heartbeat(self):
        mav = _mock_mav()
        mav.mode_mapping = MagicMock(return_value={"MANUAL": 0, "GUIDED": 15})
        heartbeat = MagicMock()
        heartbeat.get_type = MagicMock(return_value="HEARTBEAT")
        heartbeat.custom_mode = 15
        _program_ack(
            mav,
            _fake_ack(mavlink_dialect.MAV_CMD_DO_SET_MODE, result=0),
            heartbeat,
        )
        op = _make_op(SetModeRequest(mode="GUIDED"), "set_mode")
        mavlink2keelson._handle_set_mode(mav, _args(), op, 0)

        resp = SetModeResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
        assert resp.mode_actual == "GUIDED"

    def test_set_navigation_target_matching_echo_yields_accepted(self):
        mav = _mock_mav()
        target_lat_e7 = int(59.351 * 1e7)
        target_lon_e7 = int(18.071 * 1e7)
        echo = MagicMock()
        echo.get_type = MagicMock(return_value="POSITION_TARGET_GLOBAL_INT")
        echo.lat_int = target_lat_e7 + 10  # well inside tolerance
        echo.lon_int = target_lon_e7 - 50
        _program_ack(mav, echo)
        op = _make_op(
            NavigationTarget(latitude=59.351, longitude=18.071),
            "set_navigation_target",
        )
        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        resp = NavigationTargetResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED

    def test_set_navigation_target_no_echo_yields_not_observable(self):
        mav = _mock_mav()  # recv_match returns None
        op = _make_op(
            NavigationTarget(latitude=59.351, longitude=18.071),
            "set_navigation_target",
        )
        mavlink2keelson._handle_set_navigation_target(mav, _args(), op, 0)

        resp = NavigationTargetResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert resp.result == CommandResult.COMMAND_RESULT_NOT_OBSERVABLE
        assert "set_message_interval" in resp.detail

    def test_set_current_waypoint_populates_seq_actual(self):
        mav = _mock_mav()
        msg = MagicMock()
        msg.get_type = MagicMock(return_value="MISSION_CURRENT")
        msg.seq = 7
        _program_ack(mav, msg)
        op = _make_op(SetCurrentWaypointRequest(seq=7), "set_current_waypoint")
        mavlink2keelson._handle_set_current_waypoint(mav, _args(), op, 0)

        resp = SetCurrentWaypointResponse()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
        assert resp.seq_actual == 7
