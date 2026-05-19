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
)
from keelson.interfaces.VehicleLifecycle_pb2 import (
    RebootRequest,
    RebootAck,
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
    def test_procedures_include_new_rpcs(self):
        assert "set_navigation_target" in mavlink2keelson.RPC_PROCEDURES
        assert "reboot" in mavlink2keelson.RPC_PROCEDURES
