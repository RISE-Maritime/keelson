"""MAVLink 2 signing against a real ArduPilot (crowsnest-dev#314): the done-when.

An ArduPilot Rover SITL (the ``ghost-drone:hil`` image, run as ``ardurover`` alone: no bundled connector,
no bus) on the HOST network. SITL's serial ports land on loopback:

  5760  SERIAL0, the install link (ArduPilot accepts unsigned frames here whatever its key: USB)
  5762  SERIAL1, mavlink2keelson's telemetry port
  5763  SERIAL2, someone else on the vessel network

The bus is an isolated loopback zenoh router: nothing leaves the machine. Skips, naming what is missing,
without docker, the image, host networking, or with the ports in use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest
from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.payloads.SensorStatus_pb2 import SensorStatus
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as m

from _signing_rig import Bus, connector, leaks, wait, write_key
from conftest import BIN_ROOT

IMAGE = os.environ.get("SITL_IMAGE", "ghost-drone:hil")
PORTS = (5760, 5762, 5763)
REALM, VESSEL, SRC = "sitlsign", "signvessel", "mavlink-sign"
SETUP = BIN_ROOT / "mavlink-signing-setup.py"
MODE = SensorStatus.OperatingMode

pytestmark = pytest.mark.e2e


def _listening(port: int) -> bool:
    out = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
    )
    return bool(out.stdout.strip())


def _skip_reason():
    if shutil.which("docker") is None:
        return "no docker CLI"
    if (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True
        ).returncode
        != 0
    ):
        return f"no image {IMAGE} (build ghost-drone's Dockerfile, or set SITL_IMAGE)"
    busy = [p for p in PORTS if _listening(p)]
    return f"SITL ports {busy} already in use" if busy else None


def _connect(port: int, timeout: float = 90.0):
    """A MAVLink 2 client on a SITL port. Never probed with a bare connect first: each TCP connect
    takes the serial slot."""
    deadline = time.time() + timeout
    while True:
        try:
            return mavutil.mavlink_connection(
                f"tcp:127.0.0.1:{port}",
                source_system=250,
                source_component=191,
                autoreconnect=False,
            )
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(1.0)


@pytest.fixture(scope="module")
def sitl():
    reason = _skip_reason()
    if reason:
        pytest.skip(f"SITL: {reason}")
    name = f"sign-sitl-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            "host",
            "--entrypoint",
            "/usr/local/bin/ardurover",
            IMAGE,
            "-S",
            "--model",
            "rover",
            "--home",
            "57.7089,11.9746,0,270",
            "--defaults",
            "/config/rover/defaults.parm",
        ],
        capture_output=True,
        check=True,
    )
    try:
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _ack(c, timeout: float = 4.0):
    """Ask the autopilot for its AUTOPILOT_VERSION: a command it ACKs if it accepted the frame."""
    t0 = time.time()
    while (
        time.time() - t0 < 5
        and c.recv_match(type="HEARTBEAT", blocking=True, timeout=1) is None
    ):
        pass
    c.mav.command_long_send(
        1,
        1,
        m.MAV_CMD_REQUEST_MESSAGE,
        0,
        m.MAVLINK_MSG_ID_AUTOPILOT_VERSION,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = c.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.5)
        if a is not None and a.command == m.MAV_CMD_REQUEST_MESSAGE:
            return a.result
    return None


def test_a_keyed_autopilot_rejects_unsigned_and_wrongly_signed_commands_and_the_connector_signs(
    sitl, connector_process_factory, tmp_path
):
    key, wrong = os.urandom(32), os.urandom(32)
    key_file, wrong_file = tmp_path / "mavlink.key", tmp_path / "wrong.key"
    write_key(key_file, key)
    write_key(wrong_file, wrong)

    install = _connect(5760)  # SERIAL0 first: SITL opens the others after it
    time.sleep(2)
    other = _connect(5763)
    # Baseline: before keying, an unsigned command is answered -- so a silence below means refused.
    assert _ack(other) == m.MAV_RESULT_ACCEPTED
    install.close()

    setup = subprocess.run(
        [
            sys.executable,
            str(SETUP),
            "--key-file",
            str(key_file),
            "--mavlink-url",
            "tcp:127.0.0.1:5760",
            "--timeout",
            "15",
        ],
        capture_output=True,
        timeout=60,
    )
    assert setup.returncode == 0, setup.stderr.decode()[-2000:]
    assert leaks(setup.stdout + setup.stderr, key) == []

    assert _ack(other) is None, "an UNSIGNED command was accepted by a keyed autopilot"
    other.setup_signing(
        wrong, sign_outgoing=True, allow_unsigned_callback=lambda *_: True
    )
    assert _ack(other) is None, "a command signed with the WRONG key was accepted"
    other.close()

    bus = Bus(tmp_path, REALM, VESSEL, SRC)
    try:
        proc = connector(
            connector_process_factory,
            bus,
            "tcp:127.0.0.1:5762",
            tmp_path,
            "--signing-key-file",
            str(key_file),
        )
        proc.start()
        resp = bus.command()
        assert resp is not None, "the signed connector never answered send_command_long"
        assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED, resp
        if not wait(lambda: bus.signing_mode() == MODE.RUNNING, 15):
            proc.stop()
            out, err = proc.logs()
            raise AssertionError(
                f"signing status {bus.signing_mode()}, not RUNNING: {(out + err)[-3000:]}"
            )
        assert wait(
            lambda: bus.heard("vehicle_armed"), 15
        ), "no telemetry from the signed link"
        proc.stop()
        out, err = proc.logs()
        assert "MAVLink signing ON" in err + out
        assert (
            leaks((out + err).encode(), key) == []
        ), "the key is in the connector's log"
        assert bus.payload_leaks(key) == [], "the key is on the bus"
    finally:
        bus.close()

    bus = Bus(tmp_path, REALM, VESSEL, SRC)
    try:
        proc = connector(
            connector_process_factory,
            bus,
            "tcp:127.0.0.1:5762",
            tmp_path,
            "--signing-key-file",
            str(wrong_file),
        )
        proc.start()
        assert wait(lambda: bus.signing_mode() == MODE.ERROR, 20), bus.signing_mode()
        time.sleep(2)
        assert not bus.heard(
            "vehicle_armed"
        ), "telemetry from a link that does not verify"
        proc.stop()
        out, err = proc.logs()
        assert "MAVLink signing ERROR" in out + err
        assert leaks((out + err).encode(), wrong) == []
    finally:
        bus.close()
