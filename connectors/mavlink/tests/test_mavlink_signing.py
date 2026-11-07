"""MAVLink 2 signing in mavlink2keelson (crowsnest-dev#314), without docker.

The units cover the key file, pymavlink's signing as the connector sets it up, and the status mode. The
e2e runs the real connector against a fake autopilot (``_fake_signed_autopilot.py``, its own process)
on an isolated loopback: keyed or not, so the connector's required, accept-unsigned and unkeyed modes
each meet the peer they are for. The real ArduPilot is in ``test_mavlink_signing_sitl.py``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest
from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.payloads.SensorStatus_pb2 import SensorStatus
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as m

import mavlink_signing as ms
from _signing_rig import Bus, connector, free_port, leaks, wait, write_key
from conftest import mavlink2keelson

MODE = SensorStatus.OperatingMode
FAKE = pathlib.Path(__file__).with_name("_fake_signed_autopilot.py")
KEY = bytes(range(1, 33))


# ---------------------------------------------------------------------------- the key file


def test_a_key_file_reads_as_its_32_bytes(tmp_path):
    p = tmp_path / "k"
    write_key(p, KEY)
    assert ms.load_key(p) == KEY
    p.write_text(KEY.hex())  # no trailing newline
    assert ms.load_key(p) == KEY


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_a_key_others_can_read_is_refused_without_echoing_it(tmp_path, mode):
    p = tmp_path / "k"
    write_key(p, KEY)
    p.chmod(mode)
    with pytest.raises(ms.SigningKeyError) as e:
        ms.load_key(p)
    assert "chmod 600" in str(e.value) and leaks(str(e.value).encode(), KEY) == []


@pytest.mark.parametrize(
    "text", [KEY.hex()[:-2], KEY.hex() + "ab", "zz" * 32, bytes(32).hex()]
)
def test_a_malformed_or_all_zero_key_is_refused_without_echoing_it(tmp_path, text):
    p = tmp_path / "k"
    p.write_text(text + "\n")
    p.chmod(0o600)
    with pytest.raises(ms.SigningKeyError) as e:
        ms.load_key(p)
    assert text not in str(e.value) and str(p) in str(e.value)


def test_a_missing_or_irregular_key_file_is_refused(tmp_path):
    with pytest.raises(ms.SigningKeyError):
        ms.load_key(tmp_path / "absent")
    with pytest.raises(ms.SigningKeyError):
        ms.load_key(tmp_path)


def test_a_generated_key_is_owner_only_and_never_overwrites(tmp_path):
    p = tmp_path / "k"
    ms.generate_key(p)
    assert (p.stat().st_mode & 0o777) == 0o600 and len(ms.load_key(p)) == 32
    first = p.read_text()
    with pytest.raises(FileExistsError):
        ms.generate_key(p)
    assert p.read_text() == first


# ---------------------------------------------------------------------------- pymavlink as set up


class _Link:
    """A mavfile's signing surface around a bare MAVLink 2 parser: enough for ``apply``."""

    WIRE_PROTOCOL_VERSION = "2.0"

    def __init__(self, sysid):
        self.mav = m.MAVLink(None, srcSystem=sysid, srcComponent=1)

    def setup_signing(self, *a, **kw):
        mavutil.mavfile.setup_signing(self, *a, **kw)


def _frame(sender) -> bytes:
    return sender.mav.heartbeat_encode(
        m.MAV_TYPE_SURFACE_BOAT, m.MAV_AUTOPILOT_ARDUPILOTMEGA, 0, 0, 0
    ).pack(sender.mav)


def _received(receiver, frame: bytes) -> bool:
    try:
        return bool(receiver.mav.parse_buffer(frame))
    except Exception:  # noqa: BLE001 -- pymavlink raises MAVError on a refused frame
        return False


def test_a_signed_link_drops_unsigned_and_wrongly_signed_frames_and_counts_them():
    rx = _Link(255)
    ms.apply(rx, KEY, accept_unsigned=False, link_id=0)
    good, unsigned, wrong = _Link(1), _Link(1), _Link(1)
    ms.apply(good, KEY, accept_unsigned=False, link_id=0)
    wrong.setup_signing(bytes(32 * [9]), sign_outgoing=True, link_id=0)
    assert _received(rx, _frame(good))
    assert not _received(rx, _frame(unsigned))
    assert not _received(rx, _frame(wrong))
    assert ms.counts_of(rx) == ms.Counts(good=1, bad=1, unsigned=0, rejected=2)


def test_accept_unsigned_lets_an_unsigned_frame_in_and_counts_it():
    rx = _Link(255)
    ms.apply(rx, KEY, accept_unsigned=True, link_id=0)
    assert _received(rx, _frame(_Link(1)))
    assert ms.counts_of(rx).unsigned == 1


def test_what_the_connector_sends_is_signed_under_its_key():
    tx, peer = _Link(255), _Link(1)
    ms.apply(tx, KEY, accept_unsigned=False, link_id=3)
    frame = _frame(tx)
    assert frame[2] & 0x01  # MAVLINK_IFLAG_SIGNED
    peer.setup_signing(
        KEY, sign_outgoing=False, allow_unsigned_callback=lambda *_: False, link_id=0
    )
    assert _received(peer, frame) and peer.mav.signing.goodsig_count == 1


def test_signing_refuses_a_link_that_is_not_mavlink_2():
    v1 = _Link(1)
    v1.WIRE_PROTOCOL_VERSION = "1.0"
    with pytest.raises(ms.SigningError, match="MAVLink 2"):
        ms.apply(v1, KEY, accept_unsigned=False, link_id=0)


# ---------------------------------------------------------------------------- the status mode


def _mon(accept=False):
    return ms.SigningMonitor(True, accept, window_s=10)


def test_no_key_is_disabled():
    assert ms.SigningMonitor(enabled=False).sample(0.0) == ms.DISABLED


def test_nothing_heard_is_unknown_until_a_whole_window_has_passed_then_an_error():
    mon = _mon()
    assert mon.sample(0.0, ms.Counts()) == ms.UNKNOWN
    assert mon.sample(9.0, ms.Counts()) == ms.UNKNOWN
    assert mon.sample(10.0, ms.Counts()) == ms.ERROR


def test_verified_frames_are_running():
    mon = _mon()
    mon.sample(0.0, ms.Counts())
    assert mon.sample(1.0, ms.Counts(good=5)) == ms.RUNNING


@pytest.mark.parametrize(
    "bad", [ms.Counts(good=5, bad=1), ms.Counts(good=5, rejected=1)]
)
def test_one_bad_or_refused_frame_is_an_error_even_among_good_ones(bad):
    mon = _mon()
    mon.sample(0.0, ms.Counts())
    assert mon.sample(1.0, bad) == ms.ERROR
    assert "1 bad signature" in mon.summary() or "1 rejected" in mon.summary()


def test_the_window_forgets_an_old_refusal():
    mon = _mon()
    mon.sample(0.0, ms.Counts())
    assert mon.sample(1.0, ms.Counts(good=1, rejected=1)) == ms.ERROR
    for t in range(2, 12):
        mon.sample(float(t), ms.Counts(good=1 + t, rejected=1))
    assert mon.mode() == ms.RUNNING


def test_unsigned_frames_while_accepting_them_are_standby():
    mon = _mon(accept=True)
    mon.sample(0.0, ms.Counts())
    assert mon.sample(1.0, ms.Counts(unsigned=3)) == ms.STANDBY
    assert (
        mon.sample(2.0, ms.Counts(good=2, unsigned=3)) == ms.STANDBY
    )  # still unsigned in the window
    for t in range(3, 14):
        mon.sample(
            float(t), ms.Counts(good=t, unsigned=3)
        )  # the autopilot is keyed now
    assert mon.mode() == ms.RUNNING


# ---------------------------------------------------------------------------- the CLI's refusals


@pytest.mark.parametrize(
    "argv, why",
    [
        (
            ["--mavlink-url", "tcp:127.0.0.1:1", "--signing-accept-unsigned"],
            "needs --signing-key-file",
        ),
        (["--mavlink-url", "tlog:/x.tlog", "--signing-key-file", "/k"], "not a replay"),
        (
            [
                "--mavlink-url",
                "tcp:127.0.0.1:1",
                "--signing-key-file",
                "/k",
                "--signing-link-id",
                "256",
            ],
            "0-255",
        ),
    ],
)
def test_the_cli_refuses_what_cannot_be_signed(argv, why, capsys):
    with pytest.raises(SystemExit) as e:
        mavlink2keelson.main(
            ["-r", "r", "-e", "e", "-s", "s", "--target-system", "1", *argv]
        )
    assert e.value.code == 2 and why in capsys.readouterr().err


# ---------------------------------------------------------------------------- the real process


@pytest.fixture
def autopilot(tmp_path):
    procs = []

    def start(key: bytes | None):
        port = free_port()
        cmd = [sys.executable, str(FAKE), "--port", str(port)]
        if key is not None:
            write_key(tmp_path / "fake.key", key)
            cmd += ["--key-file", str(tmp_path / "fake.key")]
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        procs.append(p)
        assert p.stdout.readline().strip() == "READY"
        return f"tcp:127.0.0.1:{port}"

    yield start
    for p in procs:
        p.kill()
        p.wait()


def _run(factory, tmp_path, url, *extra):
    bus = Bus(tmp_path, "sign", "vessel", "mav")
    proc = connector(factory, bus, url, tmp_path, *extra)
    proc.start()
    return bus, proc


def _key_file(tmp_path, key=KEY):
    p = tmp_path / "mavlink.key"
    write_key(p, key)
    return str(p)


@pytest.mark.e2e
def test_keyed_both_ends_the_command_lands_and_the_status_is_running(
    connector_process_factory, tmp_path, autopilot
):
    bus, proc = _run(
        connector_process_factory,
        tmp_path,
        autopilot(KEY),
        "--signing-key-file",
        _key_file(tmp_path),
    )
    try:
        resp = bus.command()
        assert resp is not None and resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
        assert wait(lambda: bus.signing_mode() == MODE.RUNNING, 10), bus.signing_mode()
        assert wait(lambda: bus.heard("vehicle_armed"), 10)
    finally:
        proc.stop()
        bus.close()
    out, err = proc.logs()
    assert leaks((out + err).encode(), KEY) == [] and bus.payload_leaks(KEY) == []


@pytest.mark.e2e
def test_required_mode_refuses_an_unkeyed_autopilot(
    connector_process_factory, tmp_path, autopilot
):
    bus, proc = _run(
        connector_process_factory,
        tmp_path,
        autopilot(None),
        "--signing-key-file",
        _key_file(tmp_path),
    )
    try:
        assert wait(lambda: bus.signing_mode() == MODE.ERROR, 10), bus.signing_mode()
        assert not bus.heard("vehicle_armed"), "telemetry from unsigned frames"
    finally:
        proc.stop()
        bus.close()
    out, err = proc.logs()
    assert "MAVLink signing ERROR" in out + err and "rejected" in out + err


@pytest.mark.e2e
def test_accept_unsigned_is_standby_and_says_so(
    connector_process_factory, tmp_path, autopilot
):
    bus, proc = _run(
        connector_process_factory,
        tmp_path,
        autopilot(None),
        "--signing-key-file",
        _key_file(tmp_path),
        "--signing-accept-unsigned",
    )
    try:
        assert wait(lambda: bus.signing_mode() == MODE.STANDBY, 10), bus.signing_mode()
        assert wait(lambda: bus.heard("vehicle_armed"), 10)
    finally:
        proc.stop()
        bus.close()
    out, err = proc.logs()
    assert "ACCEPTING UNSIGNED" in out + err


@pytest.mark.e2e
def test_a_keyed_autopilot_drops_an_unkeyed_connectors_commands(
    connector_process_factory, tmp_path, autopilot
):
    """The fake does what a keyed ArduPilot does: nothing the unkeyed connector sends is accepted."""
    bus, proc = _run(connector_process_factory, tmp_path, autopilot(KEY))
    try:
        assert wait(lambda: bus.signing_mode() == MODE.DISABLED, 10), bus.signing_mode()
        resp = bus.command(timeout=20)
        assert resp is None or resp.result != CommandResult.COMMAND_RESULT_ACCEPTED
    finally:
        proc.stop()
        bus.close()
    out, err = proc.logs()
    assert "MAVLink signing is OFF" in out + err


@pytest.mark.e2e
def test_a_key_file_others_can_read_stops_the_connector(
    connector_process_factory, tmp_path, autopilot
):
    url = autopilot(KEY)
    key_file = _key_file(tmp_path)
    os.chmod(key_file, 0o644)
    bus, proc = _run(
        connector_process_factory, tmp_path, url, "--signing-key-file", key_file
    )
    try:
        assert proc.wait(timeout=20) == 2
    finally:
        proc.stop()
        bus.close()
    out, err = proc.logs()
    assert "Refusing to start" in out + err and leaks((out + err).encode(), KEY) == []


# ---------------------------------------------------------------------------- the setup tool

SETUP = (
    pathlib.Path(__file__).resolve().parent.parent / "bin" / "mavlink-signing-setup.py"
)


@pytest.mark.parametrize(
    "url", ["tcp:10.0.0.5:5760", "udpout:192.168.1.20:14550", "tcp:vessel.local:5760"]
)
def test_the_setup_tool_never_keys_across_a_network(tmp_path, url):
    key = tmp_path / "k"
    r = subprocess.run(
        [sys.executable, str(SETUP), "--generate", str(key), "--mavlink-url", url],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 2 and "never across a network" in r.stderr
    assert not key.exists()  # refused before a key was even made


def test_the_setup_tool_refuses_a_key_others_can_read(tmp_path):
    key = tmp_path / "k"
    write_key(key, KEY)
    key.chmod(0o644)
    r = subprocess.run(
        [
            sys.executable,
            str(SETUP),
            "--key-file",
            str(key),
            "--mavlink-url",
            "tcp:127.0.0.1:1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 2 and "chmod 600" in r.stderr
    assert leaks((r.stdout + r.stderr).encode(), KEY) == []
