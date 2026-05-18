# Getting started: driving a motorboat with Keelson

This guide walks through using `keelson-connector-mavlink` to read telemetry
from, and steer, a motorboat whose autopilot speaks MAVLink (i.e. an
ArduPilot **Rover** with the `motorboat` or `sailboat` frame, or any
PX4-based autopilot configured similarly).

You don't need prior MAVLink experience. Concepts are introduced as they
come up.

---

## Who this is for

- You have a boat with a flight-controller-style autopilot (Pixhawk,
  Cube, Matek, MicoAir, etc.) running ArduPilot Rover.
- You have, or can add, a "companion computer" on the boat — a Raspberry
  Pi or similar small Linux box wired to the autopilot's telemetry port.
- You want telemetry from the boat to appear on a Keelson realm so other
  tools (MCAP recording, Foxglove dashboards, autonomy code) can consume
  it.
- You want to drive the boat — steering and throttle — by publishing
  Keelson messages instead of holding a physical RC transmitter.

If you only have an RC link and no companion computer, this connector
isn't for you. The companion computer is the thing that actually runs
the connector.

---

## The big picture

```
            Boat                                         Shore / topside
+-------------------------+                  +--------------------------------+
| Autopilot (e.g. Cube)   |                  |                                |
|  - ArduPilot Rover      |                  |  Your Keelson tools:           |
|  - Speaks MAVLink       |                  |   - Foxglove dashboard         |
|  - Drives ESC + servos  |                  |   - MCAP recorder              |
+----------+--------------+                  |   - Autonomy / joystick code   |
           | serial (UART/USB) at 921600     |                                |
           |                                 +-------------+------------------+
+----------+--------------+                                |
| Companion computer      |                                |
|  - Raspberry Pi / NUC   |  Zenoh peer-to-peer            |
|  - Runs:                |================================+
|    mavlink2keelson      |  (Wi-Fi / cellular / radio)
+-------------------------+
```

The companion computer is the bridge:
- It reads MAVLink frames off the autopilot's serial port.
- It republishes them as Keelson messages on the Zenoh bus.
- It subscribes to Keelson command messages and sends them back to the
  autopilot as MAVLink.

Everything downstream (dashboards, recorders, your autonomy logic) just
talks to Keelson — they never need to know about MAVLink.

---

## Key concepts in two sentences each

**MAVLink** is the binary wire protocol that open-source autopilots
(ArduPilot, PX4) speak over their telemetry ports. Each MAVLink message
has a "system id" identifying the sender (the autopilot is typically `1`,
a ground station is typically `255`).

**ArduPilot** is the firmware running on the autopilot. For boats you use
the `Rover` variant with frame class set to `motorboat` (or `sailboat`).
It supports several flight modes — for manual stick-driving you want
`MANUAL` mode.

**Arming** is the autopilot's "safety on/off" switch: when armed, the
motors can output power; when disarmed, they can't, regardless of what
commands you send. The autopilot starts disarmed every boot.

**RC override** is how a ground station tells the autopilot "pretend the
RC sticks are at these values right now". That's how this connector
drives the boat — your Keelson `manual_control` messages become RC
override messages on the MAVLink side, which the autopilot then treats
exactly as if a human had moved physical sticks.

---

## What you need

### On the boat

- An autopilot (Pixhawk-class) flashed with **ArduPilot Rover 4.5** or
  later. Frame class set to `motorboat`.
- A companion computer (Raspberry Pi 4 or similar) with:
  - Network back to shore (Wi-Fi for short range, cellular/long-range
    radio for serious deployments).
  - A serial connection to the autopilot. Easiest is USB straight into
    the autopilot's USB port; cleaner-for-production is wiring the
    autopilot's `TELEM2` port to the Pi's UART via the 5-wire JST cable.
- A normal RC receiver and transmitter as a backup. **Do not skip this**
  until you're comfortable. If your network or companion computer drops,
  you want a human with sticks to be able to take over.

### On the companion computer

- Python 3.11+
- The Keelson SDK and this connector (see Install below)
- A working Zenoh network — either pointing at a Zenoh router elsewhere
  in your fleet, or running as a peer that other peers connect to. The
  Keelson docs cover both setups.

---

## Step 1 — wire the companion computer to the autopilot

Two common options:

**Option A: USB cable, Pi → autopilot USB port.** Easiest. The autopilot
shows up as `/dev/ttyACM0` (sometimes `/dev/ttyACM1`) on the Pi. Default
baud doesn't matter for USB; the connector uses 115200 in our examples
but USB ignores it.

**Option B: UART, autopilot TELEM2 → Pi GPIO serial.** More reliable than
USB for boat-grade applications, no enumeration drift. You configure the
autopilot to use TELEM2 for MAVLink at a known baud (commonly 921600),
and on the Pi the device is `/dev/serial0` (or `/dev/ttyAMA0`, depending
on Pi model).

To configure TELEM2 in ArduPilot, set these parameters (via Mission
Planner, MAVProxy, or whatever tool you use today):

```
SERIAL2_PROTOCOL = 2     # MAVLink 2
SERIAL2_BAUD     = 921   # 921600 baud; the param is in thousands, so 921 = 921600
```

Then connect, e.g.:

```bash
# (companion computer, option A)
mavlink2keelson --mavlink-url /dev/ttyACM0 --baud 115200 ...

# (companion computer, option B)
mavlink2keelson --mavlink-url /dev/serial0 --baud 921600 ...
```

---

## Step 2 — configure the autopilot (the critical part)

This step is the one most people get wrong. The connector will silently
not work if you skip it.

Set these parameters on the autopilot (Mission Planner: *Config →
Full Parameter List*; or via MAVProxy `param set NAME VALUE`):

| Parameter | Set to | Why |
| --- | --- | --- |
| `SYSID_MYGCS` | **`254`** | ArduPilot silently drops `RC_CHANNELS_OVERRIDE`, `MANUAL_CONTROL`, and most command messages from any sender whose system id doesn't match this. The connector defaults to source id 254. If `SYSID_MYGCS` stays at the factory default (255), the boat will receive every command and silently ignore it — telemetry will look healthy, the boat will not move. This is the single most common mistake. |
| `FS_GCS_ENABLE` | `1` (or higher) | When the Zenoh link from shore dies, the autopilot should fail to a safe state instead of holding the last command. See the safety section below. |
| `FS_GCS_TIMEOUT` | `5` | Seconds without a heartbeat from the GCS before failsafe triggers. |
| `FS_GCS_ACTION` | choose: `1` (HOLD) for a safe stop, `5` (SmartRTL) if you have GPS and want auto-return | The action to take when the GCS link drops. |
| `FS_THR_ENABLE` | `1` | RC failsafe — if the physical RC link is lost, the autopilot does something safe. |
| `SERIAL2_PROTOCOL` | `2` | (Option B only) Tell the autopilot to speak MAVLink 2 on TELEM2. |
| `SERIAL2_BAUD` | `921` | (Option B only) 921600 baud on TELEM2. |

Save and reboot the autopilot after changing serial settings.

> **Why 254 and not 255?** This connector defaults to source id 254 to
> avoid colliding with the older `blueos-gateway` chain (which uses 255)
> during parallel rollouts. You can pass `--source-system 255` to the
> connector instead and leave `SYSID_MYGCS` at the factory default — but
> then you can't run both connectors against the same vehicle at once.

> **Channel mapping is auto-detected.** ArduPilot routes RC input through
> `RCMAP_ROLL` (steering) and `RCMAP_THROTTLE` (throttle); the actual motor
> output goes wherever `SERVOn_FUNCTION` is configured. The connector reads
> those params on first run and caches the steering/throttle channel
> mapping under `~/.keelson/mavlink-{entity_id}.json` — you don't need to
> pass `--steering-channel` / `--throttle-channel` unless you want to
> override. If you later change `RCMAP_*` or `SERVOn_FUNCTION` on the
> autopilot, the cache's fingerprint will mismatch and the connector
> re-detects on next restart automatically.

---

## Step 3 — install and run the connector

On the companion computer:

```bash
# Clone the keelson workspace (or install just this connector via pip
# once it's published)
git clone https://github.com/RISE-Maritime/keelson.git
cd keelson
uv sync --all-packages

# Sanity check
uv run python -c "from pymavlink import mavutil; print('pymavlink ok')"
```

Run the connector. Adjust `--realm`, `--entity-id`, and the Zenoh
endpoint to match your fleet:

```bash
uv run python connectors/mavlink/bin/mavlink2keelson.py \
  --realm rise \
  --entity-id motorboat-01 \
  --source-id mav/0 \
  --mavlink-url /dev/serial0 --baud 921600 \
  --target-system 1 \
  --mode peer \
  --connect tcp/<your-zenoh-router-or-peer>:7447
```

What each argument means:

- `--realm rise` — top-level Keelson namespace. Same value as the rest
  of your fleet.
- `--entity-id motorboat-01` — this specific boat. Should be unique.
- `--source-id mav/0` — identifies this connector instance. If you ever
  run multiple producers for the same entity, give them different
  source-ids.
- `--mavlink-url /dev/serial0` — where to read MAVLink from. See the
  README for the full set of options (UDP, TCP, serial, tlog replay).
- `--target-system 1` — which MAVLink vehicle to listen to. ArduPilot
  defaults to system id `1`; leave at `1` unless you've changed it.
- `--mode peer --connect tcp/...:7447` — Zenoh peer config. The
  connector will be a peer on your Zenoh network.

Run it under systemd or a process supervisor on the companion computer
so it survives reboots. A minimal `systemd` unit:

```ini
# /etc/systemd/system/mavlink2keelson.service
[Unit]
Description=Keelson MAVLink connector
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mavlink2keelson \
  --realm rise --entity-id motorboat-01 --source-id mav/0 \
  --mavlink-url /dev/serial0 --baud 921600 \
  --target-system 1 \
  --mode peer --connect tcp/<router>:7447
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

---

## Step 4 — verify telemetry is flowing

From a shore-side machine on the same Zenoh network, subscribe to the
boat's keys and watch:

```bash
z_sub -k "rise/@v0/motorboat-01/pubsub/**"
```

Within a few seconds you should see envelopes flowing for the standard
telemetry subjects (`vehicle_mode`, `vehicle_armed`, `entity_health`,
`location_fix`, `roll_deg`, `battery_voltage_v`, `speed_over_ground_knots`,
…). If you don't, see Troubleshooting below.

To record everything to MCAP for later replay or Foxglove viewing:

```bash
uv run python connectors/mcap/bin/keelson2mcap.py \
  --key "rise/@v0/motorboat-01/**" \
  --output-folder ./recordings/ \
  --mode peer --connect tcp/<router>:7447
```

---

## Step 5 — drive the boat from Keelson

The connector accepts a broad command surface (~20 subjects + 9 RPCs;
see `README.md` for the full list). The three that get you stick-driving
are:

| Subject | Payload | Effect |
| --- | --- | --- |
| `cmd_set_mode` | `TimestampedString` with the mode name | Switch the autopilot's mode. For stick-driving send `"MANUAL"`. Other valid modes for Rover: `"HOLD"`, `"AUTO"`, `"GUIDED"`, `"RTL"`, `"SMART_RTL"`. |
| `cmd_arm` | `TimestampedBool` (`true` = arm, `false` = disarm) | Arm or disarm the motors. |
| `manual_control` | `keelson.ManualControl` with `steering` and `throttle` in `[-1.0, 1.0]` | Steer the boat. `throttle=1.0` is full forward, `throttle=-1.0` is full reverse, `steering=1.0` is hard right, `steering=-1.0` is hard left. Publish at ~10 Hz while driving — the autopilot's RC override expires after roughly 3 s of silence. |

A minimum operating sequence to start a leg looks like:

1. Publish `cmd_set_mode("MANUAL")`.
2. Wait briefly (~1 s) for the mode change to take effect.
3. Publish `cmd_arm(True)`.
4. Wait briefly (~1 s) for the arm to take effect; you'll see
   `vehicle_armed` flip to `True` in the telemetry stream.
5. Publish `manual_control(throttle=X, steering=Y)` at ~10 Hz while
   you want to drive.
6. Publish `cmd_arm(False)` when you're done.

A minimal Python example using the Keelson SDK:

```python
import time, zenoh
from keelson import construct_pubsub_key, enclose
from keelson.payloads.Primitives_pb2 import TimestampedBool, TimestampedString
from keelson.payloads.ManualControl_pb2 import ManualControl

def serialize(msg):
    msg.timestamp.GetCurrentTime()
    return enclose(msg.SerializeToString())

with zenoh.open(zenoh.Config()) as session:
    realm, entity, src = "rise", "motorboat-01", "shore-gcs"

    def pubsub(subject):
        return construct_pubsub_key(realm, entity, subject, src)

    # 1) MANUAL mode
    session.put(pubsub("cmd_set_mode"), serialize(TimestampedString(value="MANUAL")))
    time.sleep(1)

    # 2) Arm
    session.put(pubsub("cmd_arm"), serialize(TimestampedBool(value=True)))
    time.sleep(1)

    # 3) Drive forward at half throttle for 5 seconds
    mc_pub = session.declare_publisher(pubsub("manual_control"))
    end = time.time() + 5
    while time.time() < end:
        mc = ManualControl(steering=0.0, throttle=0.5)
        mc.timestamp.GetCurrentTime()
        mc_pub.put(enclose(mc.SerializeToString()))
        time.sleep(0.1)
    mc_pub.undeclare()

    # 4) Disarm
    session.put(pubsub("cmd_arm"), serialize(TimestampedBool(value=False)))
```

The end-to-end test at
`connectors/mavlink/tests/test_mavlink_e2e.py::test_sitl_manual_control_drives_vehicle`
does exactly this against ArduPilot SITL and is the best reference for
what a working flow looks like.

---

## Step 6 — beyond stick-driving

Once you've got the boat moving under manual control, the rest of the
connector's surface lets you do everything you'd otherwise need Mission
Planner / MAVProxy for. The README has the full subject + RPC contract
with payload schemas; what follows is a tour of the most useful bits.

### Drive to a coordinate

Switch to `GUIDED` mode and publish a single `cmd_goto`:

```python
from keelson.payloads.mavlink.GoToCommand_pb2 import GoToCommand

# 1) GUIDED mode first
session.put(pubsub("cmd_set_mode"), serialize(TimestampedString(value="GUIDED")))
time.sleep(1)

# 2) Send the target
gc = GoToCommand(latitude=59.351, longitude=18.071)
gc.timestamp.GetCurrentTime()
session.put(pubsub("cmd_goto"), enclose(gc.SerializeToString()))
```

The autopilot navigates there and holds. Optional fields on
`GoToCommand` let you also specify altitude, ground speed, and target
yaw.

### Upload a pre-planned mission

Send a `mavlink.Mission` (list of `MissionItem` with `MAV_CMD_NAV_*`
commands) over the `upload_mission` RPC procedure. After upload, set
the vehicle to `AUTO` mode and arm — the autopilot executes the
mission. Pair with `cmd_set_current_waypoint` to jump mid-mission and
`cmd_clear_mission` to wipe it.

### Read and write autopilot parameters

Tune rates, PIDs, throttle caps, failsafe behaviour — anything in the
parameter list — from your scripts. `get_param`, `set_param`,
`list_params`, `set_params` are all RPC procedures under
`{realm}/@v0/{entity}/@rpc/<procedure>/{source_id}`.

```python
from keelson.interfaces.MavlinkParam_pb2 import ParamGetRequest, ParamValueResponse
from keelson import construct_rpc_key

key = construct_rpc_key("rise", "motorboat-01", "get_param", "shore-gcs")
req = ParamGetRequest(name="MOT_THR_MAX")
# Issue the Zenoh get, decode the reply as ParamValueResponse.
```

`cmd_save_params(True)` writes the current parameter set to EEPROM
(survives reboot).

### Constrain to a geofence

Upload a polygon or circular fence via `upload_geofence` RPC, then
publish `cmd_enable_geofence(True)`. The autopilot enforces the fence
according to `FENCE_ACTION` (`HOLD`, `RTL`, etc.). Always test the
fence on the bench by disconnecting the GCS link or driving toward the
fence at low throttle.

### Feed external sensors into the autopilot

The `inject_*` subjects forward inbound sensor data to ArduPilot's nav
stack. Pair each with the matching ArduPilot config (the README has
the prereq table):

- `inject_gps` — companion-side GPS (e.g. ZED-F9P over USB). Set
  `GPS_TYPE=14` (MAVLink GPS) on the autopilot.
- `inject_rtcm` — RTK corrections from a topside RTK base. Bytes flow
  straight through; ArduPilot fragments / forwards to the GPS.
- `inject_velocity_body_mps` — paddlewheel or DVL ground speed.
- `inject_external_pose` — visual / RTK pose for EKF fusion.
- `inject_distance_sensor` — depth sounder, LIDAR, ultrasonic.
- `inject_battery_status` — smart-battery / BMS state from a companion.

### Emergency stop and reboot

`cmd_emergency_stop(True)` triggers `MAV_CMD_DO_FLIGHTTERMINATION`. The
autopilot disarms and stops driving outputs. Use sparingly.

`cmd_reboot(action=REBOOT)` reboots the autopilot. The MAVLink link
goes down with it; if you've run the connector under systemd it'll
restart and reconnect on its own.

### Escape hatch

`send_command_long` is a generic RPC that sends an arbitrary
`COMMAND_LONG` to the autopilot. Lets you issue MAV_CMD_* commands the
connector doesn't yet have a typed subject for, without modifying any
code.

---

## Safety — read this before going on the water

A boat with autonomous control can hurt people and property. None of the
following are optional for any deployment that matters.

1. **Always have an RC transmitter ready and a human pilot watching.**
   Configure a mode switch on your transmitter so the pilot can take
   over at any time. ArduPilot's RC input has higher priority than RC
   override when the pilot moves the sticks.
2. **Configure GCS failsafe.** Set `FS_GCS_ENABLE=1`, pick a
   `FS_GCS_ACTION` that makes sense for your operating area
   (HOLD = stop in place; SmartRTL = navigate back to launch). Test it
   on the bench by yanking the Wi-Fi cable — the boat should go to
   the configured failsafe within a few seconds.
3. **Bench-test before the water.** Lift the props off the water, run
   the full arm → manual_control → disarm sequence over Keelson, and
   confirm the prop spins in the expected direction with the expected
   throttle response. Reverse `SERVO3_REVERSED` if it spins the wrong
   way. Do this once for every new boat.
4. **First on-water test in a controlled environment.** Calm water,
   low throttle limit (`MOT_THR_MAX` set to e.g. 0.3 = 30%), short
   tether or chase boat, line of sight, observers.
5. **Watch the link.** This connector logs every received command. If
   your Zenoh fabric ever drops the boat — even briefly — RC override
   stops refreshing and the autopilot reverts to the configured
   failsafe. Don't be surprised by this; design for it.
6. **Don't leave `ARMING_CHECK=0` enabled in production.** The
   end-to-end tests disable arming checks for convenience; on a real
   boat you want those checks (GPS lock, EKF healthy, voltage OK)
   exactly as ArduPilot ships them.
7. **Treat `cmd_emergency_stop` and `cmd_reboot` carefully.** Both are
   one-way — emergency stop disarms the vehicle and flight-terminates
   the autopilot's outputs; reboot drops the MAVLink link entirely.
   Useful in genuine emergencies and during bring-up, but don't wire
   them into an autonomy loop without an explicit human guard.
8. **`set_param` writes are persistent in RAM only until you call
   `cmd_save_params(True)`** — unless you save, a reboot restores the
   old value. This is a feature, not a bug: it lets you test tuning
   changes safely.

---

## What this connector can and cannot do

### Can

- **Stream all the basic telemetry to Keelson**: position, attitude,
  mode, armed status, battery, speed, IMU, distance sensors.
- **Lifecycle**: arm / disarm, set mode, save parameters to EEPROM,
  reboot, emergency stop.
- **Drive**: stick-driving (`manual_control`), point-to-point
  navigation (`cmd_goto` in GUIDED), AUTO-mode mission execution.
- **Missions**: upload / download / clear missions, set the current
  waypoint, change cruise speed mid-mission.
- **Geofence**: upload polygon or circle fences, enable / disable
  fence enforcement.
- **Parameters**: read or write individual or bulk autopilot
  parameters from Keelson (no Mission Planner required for tuning).
- **Sensor injection**: feed companion-side GPS, RTK corrections,
  body-frame velocity, external pose, external attitude, distance
  sensors, battery state, and system time into the autopilot's nav
  stack.
- **Escape hatch**: `send_command_long` RPC sends any `COMMAND_LONG`
  not yet typed as a subject — set message intervals, run individual
  `MAV_CMD_*` operations from a script.

### Cannot (yet)

- Camera, gimbal, payload commands.
- Calibration triggers (accel / gyro / compass / baro / ESC / RC) —
  still done via Mission Planner.
- Download dataflash logs.
- Force-arm — deliberately omitted; fix the underlying pre-arm
  condition instead.

Each of these is straightforward to add — see the three patterns
(pub/sub command, simple RPC, multi-step RPC) already in
`mavlink2keelson.py`.

---

## Troubleshooting

**No telemetry appears on the Zenoh bus.**

- Confirm `mavlink2keelson` is actually running:
  `journalctl -u mavlink2keelson -f` (if you set up systemd).
- Confirm it's seeing MAVLink frames. Run with `--log-level 10` (DEBUG)
  — within a second or two you should see processed-message counts
  climbing.
- Confirm the Zenoh side is connected. Try `z_info` from the same
  network and check that the connector's session shows up.
- Wrong `--target-system`? The autopilot is `1` by default. If you've
  customized `SYSID_THISMAV` on the autopilot, pass that value.

**Telemetry works but the boat doesn't respond to commands.**

Nine times out of ten: `SYSID_MYGCS` on the autopilot doesn't match the
connector's `--source-system` (default 254). Set the parameter on the
autopilot, reboot, try again.

Other things to check:
- Is the boat actually armed? Watch `vehicle_armed` in the telemetry
  stream.
- Is the boat in `MANUAL` mode? Watch `vehicle_mode`. Other modes
  (HOLD, AUTO, LOITER) ignore manual stick input by design.
- Are you publishing `manual_control` fast enough? RC overrides expire
  after ~3 s; you want to publish at 5–10 Hz minimum.

**The boat moves the wrong way / steering is inverted.**

Standard ArduPilot fix — invert the servo on the autopilot, not in your
code. Set `SERVO1_REVERSED` (steering) or `SERVO3_REVERSED` (throttle)
to `1` and reboot.

**The vehicle disarms unexpectedly while I'm driving.**

Almost always one of: a failsafe triggered (low battery, GCS link
gap, RC link gap), or an internal pre-arm check failed mid-run. Pull
the dataflash log off the autopilot (`@SYS/dflash.bin` over MAVLink
log download, or remove the SD card) and look for the disarm reason.

**`[Errno 111] Connection refused sleeping` in the connector log.**

The autopilot isn't reachable on the `--mavlink-url` you gave. Check
the cable, the device path, and that no other process (Mission
Planner, another `mavlink2keelson`, `mavlink-routerd`) has the serial
port open.

---

## What to read next

- `README.md` (in this directory) — full reference for every CLI flag
  and the complete subject / RPC contract with payload schemas. The
  "Downlink: commands / injection / RPC" sections enumerate every
  endpoint and its MAVLink mapping.
- `tests/test_mavlink_e2e.py` — eight working end-to-end tests against
  ArduPilot SITL. `test_sitl_manual_control_drives_vehicle` is the
  worked example for stick-driving;
  `test_sitl_set_param_then_get_param_roundtrips` and
  `test_sitl_mission_upload_download_roundtrips` show the autonomy
  surface; `test_sitl_send_command_long_arms_vehicle` shows the
  escape hatch.
- ArduPilot's [Rover Failsafe](https://ardupilot.org/rover/docs/rover-failsafes.html)
  docs — read these before going on the water.
- ArduPilot's [Full parameter list for Rover](https://ardupilot.org/rover/docs/parameters.html)
  — the authoritative source for what every `FS_*`, `ARMING_*`,
  `MOT_*`, `RCMAP_*`, and `EK3_SRC*` parameter does.
