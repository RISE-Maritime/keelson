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
    ManualControlSource,
    ManualControlSources,
    ManualControlSourcesAck,
)
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
            "set_manual_control_sources",
            "get_manual_control_sources",
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
# VehicleControl: set / get manual_control sources
# ---------------------------------------------------------------------------


class TestManualControlState:
    def _make_state(self, entity_id="motorboat-01"):
        session = MagicMock()
        # declare_subscriber returns a unique handle per call so we can
        # observe undeclare ordering.
        session.declare_subscriber = MagicMock(side_effect=lambda key, cb: MagicMock())
        args = argparse.Namespace(
            realm="rise",
            entity_id=entity_id,
        )
        cmd_queue = MagicMock()
        return mavlink2keelson.ManualControlState(session, args, cmd_queue), session

    def test_starts_with_no_subscribers(self):
        state, session = self._make_state()
        assert state.get_sources() == []
        assert not session.declare_subscriber.called

    def test_set_sources_declares_subscribers(self):
        state, session = self._make_state()
        state.set_sources(
            [
                ManualControlSource(entity_id="", source_id="joystick-1"),
                ManualControlSource(entity_id="rtk-rover", source_id="**"),
            ]
        )
        assert session.declare_subscriber.call_count == 2
        # First call subscribes under the connector's own entity_id.
        first_key = session.declare_subscriber.call_args_list[0].args[0]
        assert "motorboat-01" in first_key
        assert first_key.endswith("manual_control/joystick-1")
        # Second call subscribes cross-entity.
        second_key = session.declare_subscriber.call_args_list[1].args[0]
        assert "rtk-rover" in second_key
        assert second_key.endswith("manual_control/**")

    def test_set_sources_replaces_old_subscribers(self):
        state, session = self._make_state()
        state.set_sources([ManualControlSource(source_id="joystick-1")])
        # Reconfigure -- the old subscriber should be undeclared.
        state.set_sources([ManualControlSource(source_id="joystick-2")])
        # Mock.side_effect returns a fresh MagicMock each call, so check
        # that the second declare_subscriber call was made (i.e. the
        # state actually rebuilt subscribers, not just no-op'd).
        assert session.declare_subscriber.call_count == 2

    def test_get_sources_normalises_entity_id(self):
        state, session = self._make_state(entity_id="motorboat-01")
        state.set_sources([ManualControlSource(entity_id="", source_id="joystick-1")])
        sources = state.get_sources()
        assert len(sources) == 1
        assert sources[0].entity_id == "motorboat-01"
        assert sources[0].source_id == "joystick-1"

    def test_empty_set_undeclares_all(self):
        state, session = self._make_state()
        state.set_sources([ManualControlSource(source_id="joystick-1")])
        state.set_sources([])
        assert state.get_sources() == []


class TestManualControlRpcs:
    def test_set_manual_control_sources_calls_state(self):
        state = MagicMock()
        args = argparse.Namespace(_manual_control_state=state)
        req = ManualControlSources(
            sources=[
                ManualControlSource(source_id="joystick-1"),
            ]
        )
        op = _make_op(req, "set_manual_control_sources")
        mavlink2keelson._handle_set_manual_control_sources(
            MagicMock(),
            args,
            op,
            0,
        )
        state.set_sources.assert_called_once()
        passed_sources = list(state.set_sources.call_args.args[0])
        assert len(passed_sources) == 1
        assert passed_sources[0].source_id == "joystick-1"
        op.query.reply.assert_called_once()
        ManualControlSourcesAck().ParseFromString(op.query.reply.call_args.args[1])

    def test_get_manual_control_sources_returns_state(self):
        state = MagicMock()
        state.get_sources.return_value = [
            ManualControlSource(entity_id="motorboat-01", source_id="joystick-1"),
        ]
        args = argparse.Namespace(_manual_control_state=state)
        op = _make_op(ManualControlSources(), "get_manual_control_sources")
        mavlink2keelson._handle_get_manual_control_sources(
            MagicMock(),
            args,
            op,
            0,
        )
        op.query.reply.assert_called_once()
        resp = ManualControlSources()
        resp.ParseFromString(op.query.reply.call_args.args[1])
        assert len(resp.sources) == 1
        assert resp.sources[0].entity_id == "motorboat-01"
        assert resp.sources[0].source_id == "joystick-1"
