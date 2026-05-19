"""Unit tests for the two new RPC handlers introduced by the
VehicleNavigation / VehicleLifecycle interfaces.

Both handlers go straight from a typed protobuf request to a MAVLink wire
emission + a typed reply. The tests mock pymavlink and zenoh.Query, drive
the handler directly, and assert on the wire call + reply payload.
"""

import argparse
from unittest.mock import MagicMock

import pytest
from pymavlink.dialects.v20 import ardupilotmega as mavlink_dialect

from conftest import mavlink2keelson

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
from keelson.interfaces.VehicleMission_pb2 import (
    ClearMissionRequest,
    ClearMissionAck,
    SetCurrentWaypointRequest,
    SetCurrentWaypointAck,
)
from keelson.interfaces.VehicleGeofence_pb2 import (
    EnableGeofenceRequest,
    EnableGeofenceAck,
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
    *_send method is a MagicMock so we can capture call args."""
    mav = MagicMock()
    mav.mav = MagicMock()
    return mav


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
        ack = NavigationTargetAck()
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
        ack = RebootAck()
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
        SetCruiseSpeedAck().ParseFromString(op.query.reply.call_args.args[1])


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
        ArmAck().ParseFromString(op.query.reply.call_args.args[1])


class TestSetMode:
    def _mav_with_modes(self, modes: dict[str, int]):
        mav = _mock_mav()
        mav.mode_mapping = MagicMock(return_value=modes)
        return mav

    def test_known_mode_sends_set_mode(self):
        mav = self._mav_with_modes({"MANUAL": 0, "GUIDED": 15})
        req = SetModeRequest(mode="GUIDED")
        op = _make_op(req, "set_mode")
        mavlink2keelson._handle_set_mode(mav, _args(), op, 0)

        assert mav.mav.set_mode_send.called
        call = mav.mav.set_mode_send.call_args.args
        # set_mode_send(target_system, base_mode, custom_mode)
        assert call[2] == 15
        op.query.reply.assert_called_once()
        SetModeAck().ParseFromString(op.query.reply.call_args.args[1])

    def test_unknown_mode_returns_error(self):
        mav = self._mav_with_modes({"MANUAL": 0})
        req = SetModeRequest(mode="WARP_SPEED")
        op = _make_op(req, "set_mode")
        mavlink2keelson._handle_set_mode(mav, _args(), op, 0)

        assert not mav.mav.set_mode_send.called
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
        EmergencyStopAck().ParseFromString(op.query.reply.call_args.args[1])


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
        SaveParamsAck().ParseFromString(op.query.reply.call_args.args[1])


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
        ClearMissionAck().ParseFromString(op.query.reply.call_args.args[1])


class TestSetCurrentWaypoint:
    def test_sends_mission_set_current(self):
        mav = _mock_mav()
        op = _make_op(SetCurrentWaypointRequest(seq=4), "set_current_waypoint")
        mavlink2keelson._handle_set_current_waypoint(mav, _args(), op, 0)
        call = mav.mav.mission_set_current_send.call_args.args
        # (target_sys, target_comp, seq)
        assert call[2] == 4
        op.query.reply.assert_called_once()
        SetCurrentWaypointAck().ParseFromString(op.query.reply.call_args.args[1])


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
        EnableGeofenceAck().ParseFromString(op.query.reply.call_args.args[1])


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
