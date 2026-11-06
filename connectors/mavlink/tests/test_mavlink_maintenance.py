"""Maintenance stops arming (protocols/maintenance.yaml, crowsnest-dev#304).

With --maintenance-vessel, an arm -- vehicle_lifecycle/v1 arm, and COMPONENT_ARM_DISARM through the
send_command_long escape hatch -- is refused DENIED, naming why, unless that vessel's maintenance_state is
a fresh INACTIVE. Nothing reaches the autopilot on a refusal. A disarm is never refused. Unset, nothing
changes.

The unit tests mock the MAVLink link. The e2e runs the real connector against a fake autopilot (a
pymavlink endpoint that heartbeats and ACKs COMMAND_LONG) on an ISOLATED loopback: TCP to 127.0.0.1,
zenoh with multicast and gossip scouting off.
"""

import json
import socket
import threading
import time
from unittest.mock import MagicMock

import keelson
import pytest
import zenoh
from keelson.interfaces.MavlinkCommand_pb2 import (
    CommandLongRequest,
    CommandLongResponse,
)
from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.interfaces.VehicleLifecycle_pb2 import ArmRequest, ArmResponse
from keelson.maintenance import MaintenanceWatch
from keelson.payloads.MaintenanceState_pb2 import MaintenanceState
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as m

from test_mavlink_rpcs import (
    _args,
    _fake_ack,
    _make_op,
    _mock_mav,
    _program_ack,
    mavlink2keelson,
)

V = "usv-alpha"
S = MaintenanceState.State
ARM_DISARM = m.MAV_CMD_COMPONENT_ARM_DISARM


class Clock:
    def __init__(self):
        self.t = 1_000

    def __call__(self):
        return self.t


def gated(state=None, clock=None):
    args = _args()
    args._maintenance = MaintenanceWatch(V, max_age_ms=15_000, now_ms=clock or Clock())
    if state is not None:
        args._maintenance.observe(MaintenanceState(vessel_id=V, state=state))
    return args


def arm(args, value=True):
    mav = _mock_mav()
    _program_ack(mav, _fake_ack(ARM_DISARM, result=0))
    op = _make_op(ArmRequest(arm=value), "arm")
    mavlink2keelson._handle_arm(mav, args, op, 0)
    resp = ArmResponse()
    resp.ParseFromString(op.query.reply.call_args.args[1])
    return resp, mav


def _sent_arm_disarm(mav):
    return [
        c for c in mav.mav.command_long_send.call_args_list if c.args[2] == ARM_DISARM
    ]


@pytest.mark.parametrize(
    "state",
    [
        S.STATE_UNSPECIFIED,
        S.STATE_REQUESTED,
        S.STATE_PREPARING,
        S.STATE_MAINTENANCE,
        S.STATE_READY_FOR_CHECK,
    ],
)
def test_an_arm_in_any_state_but_inactive_is_denied_and_never_sent(state):
    resp, mav = arm(gated(state))
    assert (
        resp.result == CommandResult.COMMAND_RESULT_DENIED
        and resp.raw_autopilot_result == -1
    )
    assert (
        f"arm needs maintenance_state INACTIVE: {V} is {S.Name(state).removeprefix('STATE_')}"
        in resp.detail
    )
    assert _sent_arm_disarm(mav) == []


def test_an_arm_with_nothing_heard_is_denied():
    resp, mav = arm(gated())
    assert (
        resp.result == CommandResult.COMMAND_RESULT_DENIED
        and "no maintenance_state heard" in resp.detail
    )
    assert _sent_arm_disarm(mav) == []


def test_an_arm_on_a_stale_record_is_denied():
    clock = Clock()
    args = gated(S.STATE_INACTIVE, clock)
    clock.t += 15_001
    resp, _ = arm(args)
    assert resp.result == CommandResult.COMMAND_RESULT_DENIED and "stale" in resp.detail


def test_an_arm_in_a_fresh_inactive_is_sent():
    resp, mav = arm(gated(S.STATE_INACTIVE))
    assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
    assert len(_sent_arm_disarm(mav)) == 1


@pytest.mark.parametrize("state", [None, S.STATE_MAINTENANCE])
def test_a_disarm_is_never_refused(state):
    resp, mav = arm(gated(state), value=False)
    assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
    assert _sent_arm_disarm(mav)[0].args[4] == 0.0  # param1 = 0: disarm


def test_not_opted_in_an_arm_is_sent():
    args = _args()
    assert getattr(args, "_maintenance", None) is None
    resp, mav = arm(args)
    assert (
        resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
        and len(_sent_arm_disarm(mav)) == 1
    )


def _command_long(args, command, param1):
    mav = _mock_mav()
    _program_ack(mav, _fake_ack(command, result=0))
    op = _make_op(
        CommandLongRequest(command=command, param1=param1), "send_command_long"
    )
    mavlink2keelson._handle_send_command_long(mav, args, op, 0)
    resp = CommandLongResponse()
    resp.ParseFromString(op.query.reply.call_args.args[1])
    return resp, mav


def test_the_escape_hatch_cannot_arm_around_it():
    resp, mav = _command_long(gated(S.STATE_MAINTENANCE), ARM_DISARM, 1.0)
    assert (
        resp.result == CommandResult.COMMAND_RESULT_DENIED
        and "arm (send_command_long)" in resp.detail
    )
    mav.mav.command_long_send.assert_not_called()


def test_the_escape_hatch_still_disarms_and_sends_anything_else():
    assert (
        _command_long(gated(S.STATE_MAINTENANCE), ARM_DISARM, 0.0)[0].result
        == CommandResult.COMMAND_RESULT_ACCEPTED
    )
    resp, mav = _command_long(gated(S.STATE_MAINTENANCE), m.MAV_CMD_DO_SET_SERVO, 1.0)
    assert (
        resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
        and mav.mav.command_long_send.called
    )


# ---------------------------------------------------------------------------- the real process


class FakeAutopilot:
    """A Rover on TCP: heartbeats at 2 Hz and ACKs every COMMAND_LONG, recording (command, param1)."""

    def __init__(self, port):
        self.commands = []
        self._stop = threading.Event()
        self._conn = mavutil.mavlink_connection(
            f"tcpin:127.0.0.1:{port}", source_system=1, source_component=1
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        next_hb = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_hb:
                try:
                    self._conn.mav.heartbeat_send(
                        m.MAV_TYPE_SURFACE_BOAT,
                        m.MAV_AUTOPILOT_ARDUPILOTMEGA,
                        0,
                        0,
                        m.MAV_STATE_STANDBY,
                    )
                except OSError:
                    pass  # no client connected yet
                next_hb = now + 0.5
            msg = self._conn.recv_match(type="COMMAND_LONG", blocking=True, timeout=0.1)
            if msg is not None:
                self.commands.append((msg.command, msg.param1))
                self._conn.mav.command_ack_send(msg.command, m.MAV_RESULT_ACCEPTED)

    def arms(self):
        return [p1 for c, p1 in self.commands if c == ARM_DISARM]

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._conn.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.e2e
def test_the_connector_gates_arm_on_the_live_record(
    connector_process_factory, temp_dir
):
    REALM, SRC = "e2e", "mavlink-e2e"
    zport, mport = _free_port(), _free_port()
    conf = zenoh.Config()
    conf.insert_json5("mode", json.dumps("router"))
    conf.insert_json5("scouting/multicast/enabled", "false")
    conf.insert_json5("scouting/gossip/enabled", "false")
    conf.insert_json5("listen/endpoints", json.dumps([f"tcp/127.0.0.1:{zport}"]))
    zcfg = temp_dir / "zenoh.json5"
    zcfg.write_text(
        json.dumps(
            {
                "mode": "client",
                "scouting": {
                    "multicast": {"enabled": False},
                    "gossip": {"enabled": False},
                },
                "connect": {"endpoints": [f"tcp/127.0.0.1:{zport}"]},
                "listen": {"endpoints": []},
            }
        )
    )
    autopilot = FakeAutopilot(mport)
    with zenoh.open(conf) as session:
        try:
            proc = connector_process_factory(
                "mavlink",
                "mavlink2keelson",
                [
                    "-r",
                    REALM,
                    "-e",
                    V,
                    "--source-id",
                    SRC,
                    "--mavlink-url",
                    f"tcp:127.0.0.1:{mport}",
                    "--target-system",
                    "1",
                    "--steering-channel",
                    "1",
                    "--throttle-channel",
                    "3",
                    "--config-file",
                    str(temp_dir / "ch.json"),
                    "--zenoh-config",
                    str(zcfg),
                    "--maintenance-vessel",
                    V,
                    "--maintenance-max-age-ms",
                    "60000",
                ],
            )
            proc.start()
            key = keelson.construct_rpc_key(
                REALM, V, "vehicle_lifecycle", "v1", "arm", SRC
            )

            def call(value):
                for reply in session.get(
                    key, payload=ArmRequest(arm=value).SerializeToString(), timeout=5
                ):
                    if reply.ok is not None:
                        return ArmResponse.FromString(reply.ok.payload.to_bytes())
                return None

            deadline = time.time() + 30
            resp = None
            while time.time() < deadline and resp is None:
                resp = call(True)
                time.sleep(0.3)
            if resp is None:
                proc.stop()
                out, err = proc.logs()
                raise AssertionError(
                    f"the connector never answered arm:\n{err[-4000:]}"
                )
            assert (
                resp.result == CommandResult.COMMAND_RESULT_DENIED
                and "no maintenance_state heard" in resp.detail
            )

            def maintenance(state):
                session.put(
                    keelson.construct_pubsub_key(REALM, V, "maintenance_state", V),
                    keelson.enclose(
                        MaintenanceState(vessel_id=V, state=state).SerializeToString()
                    ),
                )
                time.sleep(0.3)

            maintenance(S.STATE_MAINTENANCE)
            resp = call(True)
            assert (
                resp.result == CommandResult.COMMAND_RESULT_DENIED
                and f"{V} is MAINTENANCE" in resp.detail
            )
            assert (
                call(False).result == CommandResult.COMMAND_RESULT_ACCEPTED
            )  # a disarm passes
            assert autopilot.arms() == [
                0.0
            ]  # nothing but the disarm reached the autopilot

            maintenance(S.STATE_INACTIVE)
            assert call(True).result == CommandResult.COMMAND_RESULT_ACCEPTED
            assert autopilot.arms() == [0.0, 1.0]
        finally:
            autopilot.close()
