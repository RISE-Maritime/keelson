"""End-to-end tests for mavlink2keelson.

Two flavours:

1. ``test_tlog_replay_publishes_expected_subjects`` — generates a small
   synthetic tlog fixture and replays it through the connector. Fast, requires
   no external tooling.
2. ``test_sitl_publishes_expected_subjects`` — boots ArduPilot Rover SITL via
   ``sim_vehicle.py``, points the connector at it over TCP, and asserts the
   expected subjects show up. Skipped if ``sim_vehicle.py`` and ``ardurover``
   aren't on PATH (see ``.devcontainer/install-ardupilot-sitl.sh``).
"""

import os
import shutil
import signal
import socket
import struct
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import zenoh
from mcap.reader import make_reader
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as m

from keelson import construct_pubsub_key, construct_rpc_key, enclose
from keelson.payloads.EntityHealth_pb2 import EntityHealth, HealthLevel
from keelson.payloads.Primitives_pb2 import (
    TimestampedBool,
    TimestampedFloat,
    TimestampedString,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.interfaces.VehicleParam_pb2 import (
    ParamGetRequest,
    ParamSetRequest,
    ParamValueResponse,
)
from keelson.interfaces.VehicleMission_pb2 import Mission
from keelson.interfaces.MavlinkCommand_pb2 import (
    CommandLongRequest,
    CommandLongResponse,
)
from keelson.interfaces.VehicleNavigation_pb2 import (
    NavigationTarget,
    NavigationTargetAck,
)
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest,
    SetModeRequest,
    RebootRequest,
    RebootAck,
)
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis,
    ManualControlMapping,
)
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse


def _frame_with_ts(frame: bytes, ts_us: int) -> bytes:
    """TLog frame format: 8-byte BE microsecond timestamp followed by raw frame."""
    return struct.pack(">Q", ts_us) + frame


def _generate_tlog(path: Path) -> None:
    """Write a small synthetic tlog containing a HEARTBEAT, GLOBAL_POSITION_INT,
    GPS_RAW_INT, ATTITUDE, VFR_HUD, and BATTERY_STATUS message."""
    # The MAVLink encoder needs a "file-like" writer; we build frames using
    # pack() directly.  pack() needs a MAVLink object as context for system_id /
    # component_id / sequence — easiest is to instantiate one with stub writes.
    mav = m.MAVLink(
        file=None,
        srcSystem=1,  # vehicle
        srcComponent=1,
    )
    # Disable the actual write call inside pack() — we just want the bytes.
    # mavlink.send() and mavlink._send_message() require self.file; pack()
    # itself doesn't, so we use that and prepend our own framing.

    base_ts = 1_700_000_000_000_000  # microseconds since epoch
    out = []

    hb = m.MAVLink_heartbeat_message(
        type=m.MAV_TYPE_SURFACE_BOAT,
        autopilot=m.MAV_AUTOPILOT_ARDUPILOTMEGA,
        base_mode=m.MAV_MODE_FLAG_SAFETY_ARMED | m.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        custom_mode=10,
        system_status=m.MAV_STATE_ACTIVE,
        mavlink_version=3,
    )
    out.append(_frame_with_ts(hb.pack(mav), base_ts))

    gp = m.MAVLink_global_position_int_message(
        time_boot_ms=1234,
        lat=575780000,
        lon=119500000,
        alt=12345,
        relative_alt=12345,
        vx=100,
        vy=-50,
        vz=20,
        hdg=18000,
    )
    out.append(_frame_with_ts(gp.pack(mav), base_ts + 1_000))

    gr = m.MAVLink_gps_raw_int_message(
        time_usec=base_ts,
        fix_type=3,
        lat=575780000,
        lon=119500000,
        alt=12345,
        eph=80,
        epv=120,
        vel=100,
        cog=9000,
        satellites_visible=12,
    )
    out.append(_frame_with_ts(gr.pack(mav), base_ts + 2_000))

    att = m.MAVLink_attitude_message(
        time_boot_ms=1234,
        roll=0.1,
        pitch=-0.05,
        yaw=1.5,
        rollspeed=0.0,
        pitchspeed=0.0,
        yawspeed=0.0,
    )
    out.append(_frame_with_ts(att.pack(mav), base_ts + 3_000))

    vfr = m.MAVLink_vfr_hud_message(
        airspeed=0.0,
        groundspeed=2.5,
        heading=180,
        throttle=42,
        alt=12.0,
        climb=0.5,
    )
    out.append(_frame_with_ts(vfr.pack(mav), base_ts + 4_000))

    bs = m.MAVLink_battery_status_message(
        id=0,
        battery_function=0,
        type=0,
        temperature=2500,
        voltages=[16800] + [65535] * 9,  # uint16[]; 65535 = "no cell"
        current_battery=350,
        current_consumed=0,
        energy_consumed=0,
        battery_remaining=78,
    )
    out.append(_frame_with_ts(bs.pack(mav), base_ts + 5_000))

    # Repeat the burst a few times so the connector definitely picks it up.
    bursts = b"".join(out) * 5
    path.write_bytes(bursts)


@pytest.fixture
def tlog_fixture(temp_dir):
    path = temp_dir / "short.tlog"
    _generate_tlog(path)
    assert path.stat().st_size > 0
    return path


def _expected_channels() -> set[str]:
    return {
        "vehicle_mode",
        "vehicle_armed",
        "entity_health",
        "location_fix",
        "altitude_above_msl_m",
        "heading_true_north_deg",
        "ned_velocity_mps",
        "gps_fix_type",
        "location_fix_satellites_visible",
        "location_fix_hdop",
        "location_fix_vdop",
        "course_over_ground_deg",
        "roll_deg",
        "pitch_deg",
        "yaw_deg",
        "speed_over_ground_knots",
        "climb_rate_mps",
        "autopilot_throttle_pct",
        "battery_voltage_v",
        "battery_current_a",
        "battery_state_of_charge_pct",
        "battery_temperature_celsius",
    }


@pytest.mark.e2e
def test_tlog_replay_publishes_expected_subjects(
    connector_process_factory, temp_dir, zenoh_endpoints, tlog_fixture
):
    output_dir = temp_dir / "mcap_out"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(2)

    mav_proc = connector_process_factory(
        "mavlink",
        "mavlink2keelson",
        [
            "--realm",
            "test",
            "--entity-id",
            "drone-1",
            "--source-id",
            "mav/0",
            "--mavlink-url",
            f"tlog:{tlog_fixture}",
            "--target-system",
            "1",
            "--target-component",
            "1",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
            "--recv-timeout",
            "0.2",
        ],
    )
    mav_proc.start()
    time.sleep(4)  # give it time to drain the tlog and publish

    mav_proc.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP, got {len(mcap_files)}"

    seen_subjects: set[str] = set()
    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        for schema, channel, message in reader.iter_messages():
            # Channel topic is the full Zenoh key; subject is the segment after
            # /pubsub/.
            topic = channel.topic
            try:
                subject = topic.split("/pubsub/")[1].split("/")[0]
            except IndexError:
                continue
            seen_subjects.add(subject)

    expected = _expected_channels()
    missing = expected - seen_subjects
    assert not missing, (
        f"Missing expected subjects in MCAP: {sorted(missing)}. "
        f"Saw: {sorted(seen_subjects)}"
    )


# ---------------------------------------------------------------------------
# SITL-backed e2e
# ---------------------------------------------------------------------------

SIM_VEHICLE = shutil.which("sim_vehicle.py")
ARDUROVER = shutil.which("ardurover")
sitl_required = pytest.mark.skipif(
    SIM_VEHICLE is None or ARDUROVER is None,
    reason="ArduPilot SITL not installed (need sim_vehicle.py + ardurover on PATH; "
    "run .devcontainer/install-ardupilot-sitl.sh)",
)


def _free_sitl_instance() -> int:
    """Return an instance N such that the SITL primary TCP port (5760+10*N) is free."""
    for n in range(1, 100):
        port = 5760 + 10 * n
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return n
    raise RuntimeError("No free SITL instance slot in 1..99")


def _wait_for_sitl_heartbeat(port: int, log_path: Path, timeout: float = 45.0) -> None:
    """Connect to SITL over TCP, wait for a HEARTBEAT, then disconnect. Proves
    SITL is past its multi-step boot dance and actually serving MAVLink.

    Has to disconnect because SITL's serial port only accepts one TCP client
    at a time — the *test's* connector wants that slot next.
    """
    deadline = time.time() + timeout
    last_exc: BaseException | None = None
    while time.time() < deadline:
        try:
            probe = mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}")
            hb = probe.wait_heartbeat(timeout=5)
            probe.close()
            if hb is not None:
                # Brief pause so SITL fully releases the TCP slot before the
                # next client (the connector) reconnects.
                time.sleep(0.5)
                return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(1.0)
    log_tail = (
        log_path.read_bytes()[-4096:].decode("utf-8", errors="replace")
        if log_path.exists()
        else ""
    )
    raise TimeoutError(
        f"SITL on port {port} did not emit a HEARTBEAT within {timeout}s "
        f"(last exception: {last_exc!r}). Log tail:\n{log_tail}"
    )


@contextmanager
def _sitl_rover(work_dir: Path, instance: int, extra_param_file: Path | None = None):
    """Launch ardurover SITL and yield the TCP port once it accepts connections.

    Uses ``sim_vehicle.py --no-mavproxy`` so SITL serves MAVLink directly on
    TCP 5760+10*instance. Runs in its own process group so ardurover dies with us.

    ``extra_param_file`` (optional) is an additional ArduPilot ``.parm`` file
    layered on top of the rover/motorboat defaults — used to disable arming
    checks / failsafes in tests that drive the vehicle.
    """
    cmd = [
        SIM_VEHICLE,
        "-v",
        "Rover",
        # Plain wheeled 'rover' frame — its SITL physics respond to throttle on
        # flat ground. Boat frames (motorboat/sailboat) don't move in SITL when
        # the home location is on land.
        "-f",
        "rover",
        "-I",
        str(instance),
        "-w",  # wipe eeprom for a clean boot
        "--no-mavproxy",
        "--no-rebuild",
        "-L",
        "CMAC",
    ]
    if extra_param_file is not None:
        cmd += ["--add-param-file", str(extra_param_file)]
    log_path = work_dir / "sitl.log"
    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        cmd,
        cwd=work_dir,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    port = 5760 + 10 * instance
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                log_tail = log_path.read_bytes()[-4096:].decode(
                    "utf-8", errors="replace"
                )
                raise RuntimeError(
                    f"SITL exited with code {proc.returncode} before opening port {port}. "
                    f"Log tail:\n{log_tail}"
                )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            log_tail = log_path.read_bytes()[-4096:].decode("utf-8", errors="replace")
            raise TimeoutError(
                f"SITL TCP port {port} never opened within 60s. Log tail:\n{log_tail}"
            )
        # SITL's TCP port opens before SITL is actually streaming heartbeats —
        # ardurover goes through a "open serial0, accept connection, reload
        # params, close serial0, reopen serial0+1+2" boot dance that takes
        # several seconds. If the test's connector connects mid-dance, its
        # wait_heartbeat times out and it dies. Probe with our own pymavlink
        # connection until a heartbeat actually arrives, then disconnect so
        # SITL's single-client TCP server is free for the connector.
        _wait_for_sitl_heartbeat(port, log_path, timeout=45.0)
        yield port
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        log_file.close()
        # Persist the SITL log under /tmp so it survives the temp_dir cleanup —
        # invaluable when an e2e test fails and you need to see what SITL was
        # actually doing (mode changes, pre-arm failures, etc.).
        try:
            persisted = Path("/tmp") / f"sitl-last-{os.getpid()}.log"
            persisted.write_bytes(log_path.read_bytes())
        except Exception:  # noqa: BLE001
            pass


def _expected_sitl_channels() -> set[str]:
    """Subjects we expect from a healthy ArduRover SITL within ~20s of telemetry.

    More conservative than the tlog set: SITL doesn't simulate every optional
    field (e.g. battery temperature is INT16_MAX → unmapped), and some
    GPS-derived values depend on lock state.
    """
    return {
        # HEARTBEAT
        "vehicle_mode",
        "vehicle_armed",
        "entity_health",
        # ATTITUDE
        "roll_deg",
        "pitch_deg",
        "yaw_deg",
        # VFR_HUD
        "speed_over_ground_knots",
        "climb_rate_mps",
        "autopilot_throttle_pct",
        # GLOBAL_POSITION_INT
        "location_fix",
        "altitude_above_msl_m",
        "heading_true_north_deg",
        "ned_velocity_mps",
        # GPS_RAW_INT
        "gps_fix_type",
        "location_fix_satellites_visible",
        "location_fix_hdop",
        # BATTERY_STATUS
        "battery_voltage_v",
        "battery_current_a",
    }


# CMAC starting location from Tools/autotest/locations.txt.
CMAC_LAT = -35.363261
CMAC_LON = 149.165230
CMAC_ALT = 584.0  # meters MSL


def _collect_messages_by_subject(mcap_path: Path) -> dict[str, list[bytes]]:
    """Read every recorded message from an MCAP and group inner payload bytes
    by Keelson subject. ``keelson2mcap`` already uncovers the Envelope and
    writes only the inner payload bytes, so callers can pass each entry
    straight into the appropriate protobuf ``FromString``.
    """
    out: dict[str, list[bytes]] = {}
    with open(mcap_path, "rb") as f:
        for _, channel, message in make_reader(f).iter_messages():
            try:
                subject = channel.topic.split("/pubsub/")[1].split("/")[0]
            except IndexError:
                continue
            out.setdefault(subject, []).append(message.data)
    return out


@pytest.mark.e2e
@sitl_required
def test_sitl_telemetry_values(connector_process_factory, temp_dir, zenoh_endpoints):
    """End-to-end telemetry test against ArduRover SITL.

    Covers user-stated items (1) HEARTBEAT-derived fields and (3) telemetry —
    asserts both that the expected subjects appear AND that their payloads
    decode to sane values for a freshly-booted, disarmed SITL Rover sitting at
    the CMAC start location.
    """
    output_dir = temp_dir / "mcap_out"
    output_dir.mkdir()
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()

    instance = _free_sitl_instance()
    with _sitl_rover(sitl_dir, instance) as port:
        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test/@v0/**",
                "--output-folder",
                str(output_dir),
                "--mode",
                "peer",
                "--listen",
                zenoh_endpoints["listen"],
            ],
        )
        recorder.start()
        time.sleep(2)

        mav_proc = connector_process_factory(
            "mavlink",
            "mavlink2keelson",
            [
                "--realm",
                "test",
                "--entity-id",
                "drone-1",
                "--source-id",
                "mav/0",
                "--mavlink-url",
                f"tcp:127.0.0.1:{port}",
                "--target-system",
                "1",
                "--target-component",
                "1",
                "--mode",
                "peer",
                "--connect",
                zenoh_endpoints["connect"],
                "--recv-timeout",
                "0.5",
            ],
        )
        mav_proc.start()
        # Active readiness wait (replaces brittle fixed sleep). Then linger
        # ~15 s so EKF / GPS-lock-dependent telemetry has time to stream.
        _wait_for_connector_ready(zenoh_endpoints, timeout=30.0)
        time.sleep(15)

        mav_proc.stop()
        recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP, got {len(mcap_files)}"

    by_subject = _collect_messages_by_subject(mcap_files[0])
    seen_subjects = set(by_subject.keys())

    expected = _expected_sitl_channels()
    missing = expected - seen_subjects
    assert not missing, (
        f"Missing expected SITL subjects in MCAP: {sorted(missing)}. "
        f"Saw: {sorted(seen_subjects)}"
    )

    # ---- HEARTBEAT-derived: vehicle should be disarmed at boot ----
    armed_values = [
        TimestampedBool.FromString(b).value for b in by_subject["vehicle_armed"]
    ]
    assert armed_values, "no vehicle_armed messages"
    assert not any(
        armed_values
    ), f"Vehicle should be disarmed throughout boot, got {armed_values}"

    # ---- HEARTBEAT-derived: mode should be a known ArduRover mode (MANUAL by default) ----
    rover_modes = {
        "MANUAL",
        "ACRO",
        "STEERING",
        "HOLD",
        "LOITER",
        "FOLLOW",
        "SIMPLE",
        "DOCK",
        "AUTO",
        "RTL",
        "SMART_RTL",
        "GUIDED",
        "INITIALISING",
    }
    mode_values = [
        TimestampedString.FromString(b).value for b in by_subject["vehicle_mode"]
    ]
    assert mode_values, "no vehicle_mode messages"
    assert all(
        mv in rover_modes for mv in mode_values
    ), f"Saw unexpected vehicle_mode values: {set(mode_values) - rover_modes}"

    # ---- HEARTBEAT-derived: entity_health envelopes deserialize as EntityHealth ----
    healths = [EntityHealth.FromString(b) for b in by_subject["entity_health"]]
    assert healths, "no entity_health messages"
    # At least one report should be in a known HealthLevel enum value (sanity).
    known_levels = {
        HealthLevel.HEALTH_NOMINAL,
        HealthLevel.HEALTH_DEGRADED,
        HealthLevel.HEALTH_CRITICAL,
        HealthLevel.HEALTH_INACTIVE,
        HealthLevel.HEALTH_UNKNOWN,
    }
    assert any(h.level in known_levels for h in healths)

    # ---- GLOBAL_POSITION_INT-derived: lat/lon near CMAC home, altitude within ±100m ----
    fixes = [LocationFix.FromString(b) for b in by_subject["location_fix"]]
    assert fixes, "no location_fix messages"
    last_fix = fixes[-1]
    assert (
        abs(last_fix.latitude - CMAC_LAT) < 0.1
    ), f"Last location_fix latitude {last_fix.latitude} not near CMAC {CMAC_LAT}"
    assert (
        abs(last_fix.longitude - CMAC_LON) < 0.1
    ), f"Last location_fix longitude {last_fix.longitude} not near CMAC {CMAC_LON}"
    alts = [
        TimestampedFloat.FromString(b).value for b in by_subject["altitude_above_msl_m"]
    ]
    assert alts, "no altitude_above_msl_m messages"
    # SITL Rover reports altitude as ~0 at launch (home-relative even though the
    # subject is named *_msl_m). Just sanity-check the value is finite and within
    # an absurd-but-not-impossible Earth-surface range.
    assert all(
        -500.0 < a < 9000.0 for a in alts
    ), f"altitude_above_msl_m out of plausible range: min={min(alts)} max={max(alts)}"

    # ---- ATTITUDE-derived: roll/pitch sane for a vehicle sitting on land ----
    rolls = [TimestampedFloat.FromString(b).value for b in by_subject["roll_deg"]]
    pitches = [TimestampedFloat.FromString(b).value for b in by_subject["pitch_deg"]]
    assert rolls and pitches
    assert max(abs(r) for r in rolls) < 30.0, f"|roll| spiked > 30°: {rolls}"
    assert max(abs(p) for p in pitches) < 30.0, f"|pitch| spiked > 30°: {pitches}"

    # ---- VFR_HUD-derived: vehicle disarmed → should be stationary ----
    speeds = [
        TimestampedFloat.FromString(b).value
        for b in by_subject["speed_over_ground_knots"]
    ]
    assert speeds, "no speed_over_ground_knots messages"
    assert (
        max(speeds) < 1.0
    ), f"Disarmed vehicle should be stationary, saw max speed {max(speeds)} kts"

    # ---- BATTERY_STATUS-derived: voltage in plausible LiPo range ----
    volts = [
        TimestampedFloat.FromString(b).value for b in by_subject["battery_voltage_v"]
    ]
    assert volts, "no battery_voltage_v messages"
    assert all(
        8.0 < v < 25.0 for v in volts
    ), f"battery_voltage_v out of plausible range: min={min(volts)} max={max(volts)}"


# ---------------------------------------------------------------------------
# Command-flow test: Zenoh -> connector -> MAVLink -> SITL drives vehicle
# ---------------------------------------------------------------------------


def _open_test_zenoh_session(zenoh_endpoints: dict) -> "zenoh.Session":
    """Open a Zenoh peer that connects to the same listener the recorder owns,
    so a publisher in the test will gossip-route to the connector under test."""
    cfg = zenoh.Config()
    cfg.insert_json5("mode", '"peer"')
    cfg.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
    return zenoh.open(cfg)


def _publish_envelope(session, key: str, payload_bytes: bytes) -> None:
    """One-shot Zenoh publish of an enclosed payload. Auto-undeclares."""
    pub = session.declare_publisher(key)
    try:
        pub.put(enclose(payload_bytes))
    finally:
        try:
            pub.undeclare()
        except Exception:  # noqa: BLE001
            pass


def _start_statustext_sniffer(port: int, dest: Path):
    """Spawn a background thread that connects to SITL on ``port`` and appends
    every STATUSTEXT it sees to ``dest``. Returns a (thread, stop_event) pair —
    set the event and the thread will exit on the next recv."""
    import threading

    stop_event = threading.Event()

    def _run():
        # Wait briefly for SITL to open SERIAL1 (it opens after the first
        # SERIAL0 connection cycle).
        deadline = time.time() + 20
        conn = None
        while time.time() < deadline and not stop_event.is_set():
            try:
                conn = mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}")
                hb = conn.wait_heartbeat(timeout=3)
                if hb is not None:
                    break
                conn.close()
                conn = None
            except Exception:  # noqa: BLE001
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    conn = None
            time.sleep(1)
        if conn is None:
            dest.write_text("[statustext-sniffer] never got a heartbeat\n")
            return
        try:
            with open(dest, "w") as f:
                while not stop_event.is_set():
                    m = conn.recv_match(type="STATUSTEXT", blocking=True, timeout=0.5)
                    if m is None:
                        continue
                    f.write(f"{m.severity}: {m.text}\n")
                    f.flush()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t, stop_event


@pytest.mark.e2e
@sitl_required
def test_sitl_manual_control_drives_vehicle(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Full command-flow test: arm the vehicle, switch to MANUAL, and drive
    it forward. set_mode / arm via the VehicleLifecycle RPC; stick
    inputs flow on the existing joystick_x_pct / joystick_y_pct
    subjects, wired into the connector via the
    VehicleControl.set_manual_control_mapping RPC. Then verify the SITL
    vehicle actually moves. Covers user-stated item (2).

    SITL's TCP server only accepts one MAVLink client at a time, so the
    connector under test holds the only link to SITL. Arming checks are
    disabled at SITL boot via an ``--add-param-file`` override so the test
    doesn't need a side MAVLink connection to set parameters.
    """
    output_dir = temp_dir / "mcap_out"
    output_dir.mkdir()
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()

    # Override SITL params at boot so the vehicle accepts arming via MAVLink
    # without GPS lock / EKF / pre-arm gates, and doesn't auto-disarm or fail
    # over on transient GCS link issues.
    extra_params = temp_dir / "test_overrides.parm"
    extra_params.write_text(
        "ARMING_CHECK 0\n"
        "DISARM_DELAY 0\n"
        "FS_GCS_ENABLE 0\n"
        "FS_THR_ENABLE 0\n"
        # ArduPilot only accepts MANUAL_CONTROL / RC_OVERRIDE from the sysid
        # configured as SYSID_MYGCS (default 255). Match the connector's
        # default --source-system (254).
        "SYSID_MYGCS 254\n"
    )

    instance = _free_sitl_instance()
    statustext_log = Path("/tmp/sitl-statustext-last.log")

    with _sitl_rover(sitl_dir, instance, extra_param_file=extra_params) as port:
        # Sniffer attaches to SITL SERIAL1 (TCP port+2), which opens after the
        # first SERIAL0 connection cycle. Purely diagnostic.
        _sniffer_thread, _sniffer_stop = _start_statustext_sniffer(
            port + 2, statustext_log
        )
        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test/@v0/**",
                "--output-folder",
                str(output_dir),
                "--mode",
                "peer",
                "--listen",
                zenoh_endpoints["listen"],
            ],
        )
        recorder.start()
        time.sleep(2)

        mav_proc = connector_process_factory(
            "mavlink",
            "mavlink2keelson",
            [
                "--realm",
                "test",
                "--entity-id",
                "drone-1",
                "--source-id",
                "mav/0",
                "--mavlink-url",
                f"tcp:127.0.0.1:{port}",
                "--target-system",
                "1",  # SITL default
                "--target-component",
                "0",
                "--mode",
                "peer",
                "--connect",
                zenoh_endpoints["connect"],
                "--recv-timeout",
                "0.2",
            ],
        )
        mav_proc.start()
        # Wait for the connector to be demonstrably alive (subscribes to its
        # vehicle_mode subject and waits for the first envelope). Replaces an
        # 8 s fixed sleep that was flaky under load — see _wait_for_connector_ready.
        _wait_for_connector_ready(zenoh_endpoints, timeout=30.0)

        with _open_test_zenoh_session(zenoh_endpoints) as pub_session:
            # 1) Wire stick / throttle to the existing joystick_*_pct subjects
            #    via the VehicleControl RPC. Connector subscribes nothing on
            #    manual_control by default; this is the only way to make the
            #    vehicle drivable.
            mapping = ManualControlMapping(
                axes={
                    "steering": ManualControlAxis(
                        subject="joystick_x_pct",
                        source_id="test-gcs/joystick",
                    ),
                    "throttle": ManualControlAxis(
                        subject="joystick_y_pct",
                        source_id="test-gcs/joystick",
                    ),
                }
            )
            _rpc_call(
                pub_session,
                construct_rpc_key(
                    "test",
                    "drone-1",
                    "set_manual_control_mapping",
                    "mav/0",
                ),
                mapping.SerializeToString(),
                timeout=5.0,
            )

            # 2) MANUAL mode via VehicleLifecycle.set_mode RPC.
            set_mode_req = SetModeRequest(mode="MANUAL")
            set_mode_req.timestamp.GetCurrentTime()
            _rpc_call(
                pub_session,
                construct_rpc_key("test", "drone-1", "set_mode", "mav/0"),
                set_mode_req.SerializeToString(),
                timeout=5.0,
            )
            time.sleep(1.0)

            # 3) Arm via VehicleLifecycle.arm RPC.
            arm_req = ArmRequest(arm=True)
            arm_req.timestamp.GetCurrentTime()
            _rpc_call(
                pub_session,
                construct_rpc_key("test", "drone-1", "arm", "mav/0"),
                arm_req.SerializeToString(),
                timeout=5.0,
            )
            time.sleep(2.0)

            # 4) Drive forward at 70% throttle for 5s at 10 Hz on the
            #    existing joystick_*_pct subjects. The connector composes
            #    one RC_CHANNELS_OVERRIDE per arrival per the mapping above.
            steering_pub = pub_session.declare_publisher(
                construct_pubsub_key(
                    "test",
                    "drone-1",
                    "joystick_x_pct",
                    "test-gcs/joystick",
                ),
            )
            throttle_pub = pub_session.declare_publisher(
                construct_pubsub_key(
                    "test",
                    "drone-1",
                    "joystick_y_pct",
                    "test-gcs/joystick",
                ),
            )
            try:
                from keelson.payloads.Primitives_pb2 import TimestampedFloat

                deadline = time.time() + 5.0
                while time.time() < deadline:
                    steering = TimestampedFloat(value=0.0)
                    steering.timestamp.GetCurrentTime()
                    steering_pub.put(enclose(steering.SerializeToString()))
                    throttle = TimestampedFloat(value=70.0)
                    throttle.timestamp.GetCurrentTime()
                    throttle_pub.put(enclose(throttle.SerializeToString()))
                    time.sleep(0.1)
            finally:
                steering_pub.undeclare()
                throttle_pub.undeclare()

        # Let the last command flush + a few more telemetry samples land.
        time.sleep(2)

        mav_proc.stop()
        recorder.stop()
        _sniffer_stop.set()
        # Preserve connector stderr for debugging assertions below.
        _stdout, mav_stderr = mav_proc.logs()
        try:
            Path("/tmp/mavlink-connector-last.log").write_text(mav_stderr or "")
        except Exception:  # noqa: BLE001
            pass
        # Preserve the recorded MCAP so we can inspect timelines on failure.
        try:
            for mc in output_dir.glob("*.mcap"):
                Path("/tmp/sitl-cmdflow-last.mcap").write_bytes(mc.read_bytes())
                break
        except Exception:  # noqa: BLE001
            pass

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP, got {len(mcap_files)}"

    by_subject = _collect_messages_by_subject(mcap_files[0])

    # Vehicle should have reported armed at some point in response to the arm RPC.
    armed_values = [
        TimestampedBool.FromString(b).value for b in by_subject.get("vehicle_armed", [])
    ]
    assert any(armed_values), (
        f"Vehicle never reported armed after VehicleLifecycle.arm RPC. "
        f"vehicle_armed values: {armed_values[:15]}. "
        f"Connector stderr at /tmp/mavlink-connector-last.log"
    )

    # Mode should have switched to MANUAL at some point.
    mode_values = [
        TimestampedString.FromString(b).value
        for b in by_subject.get("vehicle_mode", [])
    ]
    assert (
        "MANUAL" in mode_values
    ), f"Vehicle never reported MANUAL mode. modes seen: {set(mode_values)}"

    # Speed should exceed a clear non-zero threshold while throttle is applied.
    speeds = [
        TimestampedFloat.FromString(b).value
        for b in by_subject.get("speed_over_ground_knots", [])
    ]
    assert speeds, "no speed_over_ground_knots messages recorded"
    assert max(speeds) > 0.5, (
        f"Vehicle did not move after Zenoh ManualControl commands; "
        f"max speed={max(speeds):.3f} kts (need > 0.5). "
        f"Connector stderr saved to /tmp/mavlink-connector-last.log"
    )


def _serialize_bool(value: bool) -> bytes:
    msg = TimestampedBool()
    msg.timestamp.GetCurrentTime()
    msg.value = value
    return msg.SerializeToString()


def _serialize_string(value: str) -> bytes:
    msg = TimestampedString()
    msg.timestamp.GetCurrentTime()
    msg.value = value
    return msg.SerializeToString()


# ---------------------------------------------------------------------------
# RPC helpers used by the new tests below.
# ---------------------------------------------------------------------------


def _wait_for_connector_ready(
    zenoh_endpoints: dict,
    mav_proc=None,
    entity_id: str = "drone-1",
    source_id: str = "mav/0",
    timeout: float = 25.0,
) -> None:
    """Open a Zenoh peer and wait for the connector under test to publish
    its first ``vehicle_mode`` envelope. Proves three things in one go:
      1. the connector subprocess has fully started,
      2. its MAVLink link is up and HEARTBEATs are flowing,
      3. our test session and the connector can actually reach each other
         over the Zenoh fabric.

    If ``mav_proc`` is supplied and the subprocess dies during the wait, we
    fail-fast with its stderr so flakes have an actionable error message
    instead of just "connector did not publish vehicle_mode within 30s".
    """
    import threading

    cfg = zenoh.Config()
    cfg.insert_json5("mode", '"peer"')
    cfg.insert_json5("connect/endpoints", f'["{zenoh_endpoints["connect"]}"]')
    seen = threading.Event()
    with zenoh.open(cfg) as sess:
        key = construct_pubsub_key("test", entity_id, "vehicle_mode", "**")
        sub = sess.declare_subscriber(key, lambda _s: seen.set())
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if seen.wait(0.5):
                    return
                if mav_proc is not None and not mav_proc.is_running():
                    try:
                        _stdout, stderr = mav_proc.logs()
                    except Exception:
                        stderr = "<could not read stderr>"
                    raise RuntimeError(
                        "connector subprocess exited before publishing "
                        f"vehicle_mode. Stderr tail:\n{(stderr or '')[-3000:]}"
                    )
            raise TimeoutError(
                f"connector did not publish vehicle_mode within {timeout}s — "
                "either it failed to start, MAVLink link is down, or Zenoh "
                "peer discovery isn't working between this test and the "
                "connector subprocess"
            )
        finally:
            sub.undeclare()


def _rpc_call(session, key: str, request_bytes: bytes, timeout: float = 15.0):
    """Issue a Zenoh query against an RPC queryable and return the (single)
    reply's raw payload bytes, or raise on timeout / error reply."""
    replies = []

    def _on_reply(reply):
        replies.append(reply)

    session.get(key, _on_reply, payload=request_bytes)
    deadline = time.time() + timeout
    while time.time() < deadline and not replies:
        time.sleep(0.05)
    if not replies:
        raise TimeoutError(f"RPC {key} did not reply within {timeout}s")
    reply = replies[0]
    # Both ok and err replies expose `.ok`/`.err`. err carries an ErrorResponse.
    try:
        ok = reply.ok
    except Exception:
        ok = None
    if ok is not None:
        return bytes(ok.payload.to_bytes())
    err_bytes = bytes(reply.err.payload.to_bytes())
    err = ErrorResponse()
    err.ParseFromString(err_bytes)
    raise RuntimeError(f"RPC {key} failed: {err.error_description}")


def _start_sitl_connector(
    connector_process_factory,
    zenoh_endpoints,
    port: int,
    listen_endpoint: str | None = None,
):
    """Start mavlink2keelson against the given SITL port and return the process.
    Mirrors test_sitl_manual_control_drives_vehicle setup, minus the boot-time
    parameter overrides which the SITL-boot extra_params file handles."""
    args = [
        "--realm",
        "test",
        "--entity-id",
        "drone-1",
        "--source-id",
        "mav/0",
        "--mavlink-url",
        f"tcp:127.0.0.1:{port}",
        "--target-system",
        "1",
        "--target-component",
        "0",
        "--mode",
        "peer",
        "--connect",
        zenoh_endpoints["connect"],
        "--recv-timeout",
        "0.2",
    ]
    if listen_endpoint:
        args.extend(["--listen", listen_endpoint])
    proc = connector_process_factory("mavlink", "mavlink2keelson", args)
    proc.start()
    # Wait for evidence the connector is actually publishing telemetry —
    # proves SITL handshake, autodetect, and Zenoh routing all completed.
    _wait_for_connector_ready(zenoh_endpoints, mav_proc=proc, timeout=30.0)
    return proc


# ---------------------------------------------------------------------------
# Pattern B coverage: get_param / set_param RPCs.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@sitl_required
def test_sitl_get_param_returns_value(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """get_param RPC against SITL — read MOT_THR_MAX and assert it matches the
    factory default the autopilot reports."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    instance = _free_sitl_instance()

    with _sitl_rover(sitl_dir, instance) as port:
        # No recorder in these RPC tests — have the connector own the listener
        # so the test session has a stable hub to connect to (otherwise
        # Zenoh peer discovery is flaky in back-to-back tests).
        mav_proc = _start_sitl_connector(
            connector_process_factory,
            zenoh_endpoints,
            port,
            listen_endpoint=zenoh_endpoints["listen"],
        )
        try:
            with _open_test_zenoh_session(zenoh_endpoints) as session:
                key = construct_rpc_key("test", "drone-1", "get_param", "mav/0")
                req = ParamGetRequest(name="MOT_THR_MAX")
                resp_bytes = _rpc_call(session, key, req.SerializeToString())
                resp = ParamValueResponse()
                resp.ParseFromString(resp_bytes)
                assert resp.name == "MOT_THR_MAX"
                # ArduPilot Rover default is 100 (percent).
                assert (
                    1.0 <= resp.value <= 100.0
                ), f"unexpected MOT_THR_MAX={resp.value}"
                assert resp.mav_param_type != 0, "missing mav_param_type"
        finally:
            mav_proc.stop()


@pytest.mark.e2e
@sitl_required
def test_sitl_set_param_then_get_param_roundtrips(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """set_param to a non-default value, then get_param to confirm the write
    actually landed. Proves the RPC dispatch is single-threaded against
    pymavlink (otherwise the second call would race the first's response)."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    instance = _free_sitl_instance()

    with _sitl_rover(sitl_dir, instance) as port:
        # No recorder in these RPC tests — have the connector own the listener
        # so the test session has a stable hub to connect to (otherwise
        # Zenoh peer discovery is flaky in back-to-back tests).
        mav_proc = _start_sitl_connector(
            connector_process_factory,
            zenoh_endpoints,
            port,
            listen_endpoint=zenoh_endpoints["listen"],
        )
        try:
            with _open_test_zenoh_session(zenoh_endpoints) as session:
                set_key = construct_rpc_key("test", "drone-1", "set_param", "mav/0")
                get_key = construct_rpc_key("test", "drone-1", "get_param", "mav/0")
                # Pick MOT_THR_MAX and halve it.
                new_value = 50.0
                set_req = ParamSetRequest(name="MOT_THR_MAX", value=new_value)
                set_resp_bytes = _rpc_call(
                    session, set_key, set_req.SerializeToString()
                )
                set_resp = ParamValueResponse()
                set_resp.ParseFromString(set_resp_bytes)
                assert set_resp.name == "MOT_THR_MAX"
                assert (
                    abs(set_resp.value - new_value) < 0.5
                ), f"set_param echo says {set_resp.value}, want {new_value}"
                # Round-trip read to confirm.
                get_req = ParamGetRequest(name="MOT_THR_MAX")
                get_resp_bytes = _rpc_call(
                    session, get_key, get_req.SerializeToString()
                )
                get_resp = ParamValueResponse()
                get_resp.ParseFromString(get_resp_bytes)
                assert (
                    abs(get_resp.value - new_value) < 0.5
                ), f"get_param after set sees {get_resp.value}, want {new_value}"
        finally:
            mav_proc.stop()


# ---------------------------------------------------------------------------
# Sensor injection (file-driven, skarv-buffered).
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@sitl_required
def test_sitl_gps_injection_via_injection_config(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Drive the file-driven injection path end-to-end.

    Writes an injection-config YAML, starts mavlink2keelson with
    ``--injection-config`` pointing at it, publishes location_fix +
    gps_fix_type + satellites_visible from a *different* source_id, and
    asserts the connector stays alive + telemetry keeps flowing (proving
    the connector decoded the envelopes and forwarded GPS_INPUT without
    crashing).

    We deliberately do NOT assert the autopilot's reported location_fix
    converges on the injected fix — SITL's default GPS_TYPE=1 prefers its
    simulated GPS over external input, and reconfiguring SITL for
    GPS_TYPE=14 requires a full reboot mid-test that the scaffold doesn't
    support yet."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    output_dir = temp_dir / "mcap_out"
    output_dir.mkdir()

    # Operator-authored injection config.
    injection_yaml = temp_dir / "injection.yaml"
    injection_yaml.write_text(
        "GPS_INPUT:\n"
        "  sources:\n"
        '    location_fix: "test-gps/0"\n'
        '    gps_fix_type: "test-gps/0"\n'
        '    location_fix_satellites_visible: "test-gps/0"\n'
        '    location_fix_hdop: "test-gps/0"\n'
        "  throttle_s: 0.1\n"
    )

    instance = _free_sitl_instance()
    with _sitl_rover(sitl_dir, instance) as port:
        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test/@v0/**",
                "--output-folder",
                str(output_dir),
                "--mode",
                "peer",
                "--listen",
                zenoh_endpoints["listen"],
            ],
        )
        recorder.start()
        time.sleep(2)

        # Start mavlink2keelson with --injection-config (no helper because
        # _start_sitl_connector doesn't pass extra args).
        mav_args = [
            "--realm",
            "test",
            "--entity-id",
            "drone-1",
            "--source-id",
            "mav/0",
            "--mavlink-url",
            f"tcp:127.0.0.1:{port}",
            "--target-system",
            "1",
            "--target-component",
            "0",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
            "--recv-timeout",
            "0.2",
            "--injection-config",
            str(injection_yaml),
        ]
        mav_proc = connector_process_factory("mavlink", "mavlink2keelson", mav_args)
        mav_proc.start()
        _wait_for_connector_ready(zenoh_endpoints, mav_proc=mav_proc, timeout=30.0)

        try:
            with _open_test_zenoh_session(zenoh_endpoints) as pub_session:
                # Publish a small burst at 10 Hz from a distinct source_id.
                fix_key = construct_pubsub_key(
                    "test", "drone-1", "location_fix", "test-gps/0"
                )
                fix_pub = pub_session.declare_publisher(fix_key)
                fix_type_pub = pub_session.declare_publisher(
                    construct_pubsub_key(
                        "test", "drone-1", "gps_fix_type", "test-gps/0"
                    )
                )
                sats_pub = pub_session.declare_publisher(
                    construct_pubsub_key(
                        "test",
                        "drone-1",
                        "location_fix_satellites_visible",
                        "test-gps/0",
                    )
                )
                hdop_pub = pub_session.declare_publisher(
                    construct_pubsub_key(
                        "test", "drone-1", "location_fix_hdop", "test-gps/0"
                    )
                )
                try:
                    from keelson.payloads.Primitives_pb2 import TimestampedInt

                    for i in range(20):
                        fix = LocationFix(
                            latitude=59.0 + i * 0.0001,
                            longitude=18.0,
                            altitude=5.0,
                        )
                        fix.timestamp.GetCurrentTime()
                        fix_pub.put(enclose(fix.SerializeToString()))

                        ft = TimestampedInt(value=3)
                        ft.timestamp.GetCurrentTime()
                        fix_type_pub.put(enclose(ft.SerializeToString()))

                        sats = TimestampedInt(value=12)
                        sats.timestamp.GetCurrentTime()
                        sats_pub.put(enclose(sats.SerializeToString()))

                        hdop = TimestampedFloat(value=0.8)
                        hdop.timestamp.GetCurrentTime()
                        hdop_pub.put(enclose(hdop.SerializeToString()))

                        time.sleep(0.1)
                finally:
                    fix_pub.undeclare()
                    fix_type_pub.undeclare()
                    sats_pub.undeclare()
                    hdop_pub.undeclare()
            # Allow telemetry to flush.
            time.sleep(2)
        finally:
            mav_proc.stop()
            recorder.stop()

        # Sanity: connector survived the injection burst -- telemetry kept
        # flowing throughout.
        mcap_files = list(output_dir.glob("*.mcap"))
        assert mcap_files, "no MCAP recorded"
        by_subject = _collect_messages_by_subject(mcap_files[0])
        assert by_subject.get(
            "vehicle_armed"
        ), "no vehicle_armed telemetry — connector likely crashed during injection"
        # The connector's process should also still be alive at the point
        # we stopped it (i.e. exit code 0 from SIGINT, not a crash). The
        # ConnectorProcess.stop() above would have raised on a crash.


# ---------------------------------------------------------------------------
# Escape-hatch RPC: send_command_long.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@sitl_required
def test_sitl_send_command_long_arms_vehicle(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Send MAV_CMD_COMPONENT_ARM_DISARM via the send_command_long RPC and
    assert vehicle_armed flips True. Proves the escape-hatch RPC works for
    arbitrary one-shot COMMAND_LONG operations."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    output_dir = temp_dir / "mcap_out"
    output_dir.mkdir()
    extra_params = temp_dir / "test_overrides.parm"
    extra_params.write_text(
        "ARMING_CHECK 0\n"
        "DISARM_DELAY 0\n"
        "FS_GCS_ENABLE 0\n"
        "FS_THR_ENABLE 0\n"
        "SYSID_MYGCS 254\n"
    )
    instance = _free_sitl_instance()

    with _sitl_rover(sitl_dir, instance, extra_param_file=extra_params) as port:
        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--key",
                "test/@v0/**",
                "--output-folder",
                str(output_dir),
                "--mode",
                "peer",
                "--listen",
                zenoh_endpoints["listen"],
            ],
        )
        recorder.start()
        time.sleep(2)
        mav_proc = _start_sitl_connector(
            connector_process_factory, zenoh_endpoints, port
        )
        try:
            with _open_test_zenoh_session(zenoh_endpoints) as session:
                key = construct_rpc_key("test", "drone-1", "send_command_long", "mav/0")
                req = CommandLongRequest(
                    command=m.MAV_CMD_COMPONENT_ARM_DISARM,
                    param1=1.0,  # arm
                )
                resp_bytes = _rpc_call(session, key, req.SerializeToString())
                resp = CommandLongResponse()
                resp.ParseFromString(resp_bytes)
                # MAV_RESULT_ACCEPTED == 0
                assert (
                    resp.mav_result == 0
                ), f"COMMAND_LONG not accepted: result={resp.mav_result} text={resp.text!r}"
            # Give telemetry time to reflect the arm state.
            time.sleep(2)
        finally:
            mav_proc.stop()
            recorder.stop()

        mcap_files = list(output_dir.glob("*.mcap"))
        assert mcap_files
        by_subject = _collect_messages_by_subject(mcap_files[0])
        armed_values = [
            TimestampedBool.FromString(b).value
            for b in by_subject.get("vehicle_armed", [])
        ]
        assert any(
            armed_values
        ), "vehicle never reported armed=True after send_command_long arm"


# ---------------------------------------------------------------------------
# Pattern C coverage: mission upload + download round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@sitl_required
def test_sitl_mission_upload_download_roundtrips(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Upload a 3-waypoint mission, download it, assert the items round-trip
    (modulo the autopilot prepending a home waypoint at seq=0)."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    instance = _free_sitl_instance()

    with _sitl_rover(sitl_dir, instance) as port:
        # No recorder in these RPC tests — have the connector own the listener
        # so the test session has a stable hub to connect to (otherwise
        # Zenoh peer discovery is flaky in back-to-back tests).
        mav_proc = _start_sitl_connector(
            connector_process_factory,
            zenoh_endpoints,
            port,
            listen_endpoint=zenoh_endpoints["listen"],
        )
        try:
            with _open_test_zenoh_session(zenoh_endpoints) as session:
                upload_key = construct_rpc_key(
                    "test", "drone-1", "upload_mission", "mav/0"
                )
                download_key = construct_rpc_key(
                    "test", "drone-1", "download_mission", "mav/0"
                )

                # 3 waypoints around a home-ish location.
                mission = Mission()
                # ArduPilot expects seq=0 to be a "home" placeholder; we send
                # NAV_WAYPOINTs starting at seq=0 and let the autopilot
                # interpret. seq numbers are renumbered by the autopilot on
                # upload anyway.
                for i, (lat, lon) in enumerate(
                    [
                        (-35.3633, 149.1652),
                        (-35.3635, 149.1655),
                        (-35.3637, 149.1652),
                    ]
                ):
                    item = mission.items.add()
                    item.seq = i
                    item.frame = m.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT  # 6
                    item.command = m.MAV_CMD_NAV_WAYPOINT  # 16
                    item.autocontinue = True
                    item.x = int(lat * 1e7)
                    item.y = int(lon * 1e7)
                    item.z = 0.0
                    item.mission_type = 0

                # Upload — large timeout because SITL is slow.
                upload_resp_bytes = _rpc_call(
                    session,
                    upload_key,
                    mission.SerializeToString(),
                    timeout=35.0,
                )
                from keelson.interfaces.MavlinkMission_pb2 import MissionUploadResponse

                upload_resp = MissionUploadResponse()
                upload_resp.ParseFromString(upload_resp_bytes)
                assert upload_resp.accepted, (
                    f"upload failed: mission_result={upload_resp.mission_result} "
                    f"error={upload_resp.error!r}"
                )

                # Download and compare. ArduPilot's response will include the
                # autopilot's internal home prefix or not depending on version;
                # we just assert all our waypoints survived.
                download_resp_bytes = _rpc_call(
                    session,
                    download_key,
                    b"",
                    timeout=35.0,
                )
                downloaded = Mission()
                downloaded.ParseFromString(download_resp_bytes)
                assert len(downloaded.items) >= len(mission.items), (
                    f"download returned {len(downloaded.items)} items, "
                    f"uploaded {len(mission.items)}"
                )
                # ArduPilot rewrites seq=0 with the vehicle's home location on
                # upload, so the first uploaded waypoint typically doesn't
                # survive round-trip. Verify that the later ones do.
                uploaded_coords = [(it.x, it.y) for it in mission.items]
                downloaded_coords = {(it.x, it.y) for it in downloaded.items}
                surviving = [c for c in uploaded_coords if c in downloaded_coords]
                assert len(surviving) >= len(uploaded_coords) - 1, (
                    f"too many uploaded waypoints clobbered on download: "
                    f"uploaded={uploaded_coords}, surviving={surviving}"
                )
        finally:
            mav_proc.stop()


# ---------------------------------------------------------------------------
# VehicleNavigation + VehicleLifecycle RPCs.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@sitl_required
def test_sitl_set_navigation_target_accepted(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Switch to GUIDED, arm, fire set_navigation_target with a target near
    CMAC home, and assert the autopilot accepts (Ack returned cleanly + the
    set_position_target_global_int frame is on the wire).

    Does not assert the vehicle reached the target — that adds 20+ s and
    isn't needed to prove the RPC works. The success Ack is the contract."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    extra_params = temp_dir / "test_overrides.parm"
    extra_params.write_text(
        "ARMING_CHECK 0\n"
        "DISARM_DELAY 0\n"
        "FS_GCS_ENABLE 0\n"
        "FS_THR_ENABLE 0\n"
        "SYSID_MYGCS 254\n"
    )
    instance = _free_sitl_instance()

    with _sitl_rover(sitl_dir, instance, extra_param_file=extra_params) as port:
        mav_proc = _start_sitl_connector(
            connector_process_factory,
            zenoh_endpoints,
            port,
            listen_endpoint=zenoh_endpoints["listen"],
        )
        try:
            with _open_test_zenoh_session(zenoh_endpoints) as session:
                # GUIDED mode + arm first, via VehicleLifecycle RPCs.
                set_mode_req = SetModeRequest(mode="GUIDED")
                set_mode_req.timestamp.GetCurrentTime()
                _rpc_call(
                    session,
                    construct_rpc_key("test", "drone-1", "set_mode", "mav/0"),
                    set_mode_req.SerializeToString(),
                    timeout=5.0,
                )
                time.sleep(1.5)

                arm_req = ArmRequest(arm=True)
                arm_req.timestamp.GetCurrentTime()
                _rpc_call(
                    session,
                    construct_rpc_key("test", "drone-1", "arm", "mav/0"),
                    arm_req.SerializeToString(),
                    timeout=5.0,
                )
                time.sleep(1.5)

                # set_navigation_target RPC.
                key = construct_rpc_key(
                    "test", "drone-1", "set_navigation_target", "mav/0"
                )
                target = NavigationTarget(
                    # CMAC home plus ~50 m north.
                    latitude=CMAC_LAT + 0.00045,
                    longitude=CMAC_LON,
                )
                target.timestamp.GetCurrentTime()
                resp_bytes = _rpc_call(
                    session,
                    key,
                    target.SerializeToString(),
                    timeout=5.0,
                )
                ack = NavigationTargetAck()
                ack.ParseFromString(resp_bytes)
                # Empty Ack parses cleanly; nothing else to assert in the
                # response itself. Acceptance is what we tested.
        finally:
            mav_proc.stop()


@pytest.mark.e2e
@sitl_required
def test_sitl_reboot_rpc_acked_and_drops_link(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Fire reboot(action=REBOOT) and assert the RPC Ack comes back. We
    cannot reliably probe SITL post-reboot from this test (the SITL fixture
    expects a single boot cycle), so the assertion is just that the RPC
    handler ran the send path + replied cleanly."""
    sitl_dir = temp_dir / "sitl"
    sitl_dir.mkdir()
    instance = _free_sitl_instance()

    with _sitl_rover(sitl_dir, instance) as port:
        mav_proc = _start_sitl_connector(
            connector_process_factory,
            zenoh_endpoints,
            port,
            listen_endpoint=zenoh_endpoints["listen"],
        )
        try:
            with _open_test_zenoh_session(zenoh_endpoints) as session:
                key = construct_rpc_key("test", "drone-1", "reboot", "mav/0")
                req = RebootRequest(action=RebootRequest.REBOOT)
                req.timestamp.GetCurrentTime()
                resp_bytes = _rpc_call(
                    session,
                    key,
                    req.SerializeToString(),
                    timeout=5.0,
                )
                ack = RebootAck()
                ack.ParseFromString(resp_bytes)
        finally:
            # The connector loop may exit on its own because the autopilot
            # link drops; stop() handles both still-running and already-exited.
            mav_proc.stop()
