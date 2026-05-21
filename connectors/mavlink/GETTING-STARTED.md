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
drives the boat — you publish to standard Keelson controller-input
subjects (`joystick_x_pct`, `joystick_y_pct`, etc.) and the connector
turns them into RC override messages on the MAVLink side, which the
autopilot then treats exactly as if a human had moved physical sticks.

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
> mapping under `${KEELSON_STATE_DIR:-~/.keelson}/mavlink-{entity_id}.json`
> — you don't need to pass `--steering-channel` / `--throttle-channel`
> unless you want to override. If you later change `RCMAP_ROLL` /
> `RCMAP_THROTTLE` / `FRAME_CLASS` / `FRAME_TYPE` on the autopilot, the
> cache's fingerprint will mismatch and the connector re-detects on next
> restart automatically.
>
> In Docker, set `KEELSON_STATE_DIR` to a mounted volume so the cache
> survives container restarts — otherwise it lives under `/root/.keelson`
> inside the container and is lost on every recreation.

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

The connector's command surface is **all RPC**. Stick-driving values
themselves flow on existing controller-input subjects
(`joystick_x_pct`, `joystick_y_pct`, etc.), wired to the autopilot's
RC channels via a one-shot `set_manual_control_mapping` RPC call.

| Endpoint | Kind | Effect |
| --- | --- | --- |
| `VehicleControl.set_manual_control_mapping` | RPC | Tell the connector which Keelson subjects represent steering and throttle (one-shot at the start of a session). Replaces any previously-installed mapping atomically. |
| `VehicleLifecycle.set_mode` | RPC | Switch the autopilot's mode. For stick-driving send `"MANUAL"`. Other valid modes for Rover: `"HOLD"`, `"AUTO"`, `"GUIDED"`, `"RTL"`, `"SMART_RTL"`. |
| `VehicleLifecycle.arm` | RPC | `ArmRequest(arm=true/false)` — arm or disarm the motors. |
| `joystick_x_pct`, `joystick_y_pct` (or whichever subjects you map) | Pub/sub | `keelson.TimestampedFloat` values in `[-100, 100]`. `+100` on the throttle axis is full forward, `-100` is full reverse, `+100` on the steering axis is hard right. Publish at ~10 Hz while driving — the autopilot's RC override expires after roughly 3 s of silence. |

The connector subscribes to **no** stick-driving subjects by default;
calling `set_manual_control_mapping` is the only way to make the
vehicle drivable.

A minimum operating sequence to start a leg:

1. Call `set_manual_control_mapping` with the subjects your producer
   publishes to (e.g. `steering` ← `joystick_x_pct/joystick-1`,
   `throttle` ← `joystick_y_pct/joystick-1`).
2. Call `set_mode("MANUAL")`. Wait briefly (~1 s) for the mode change.
3. Call `arm(arm=true)`. Wait briefly (~1 s) for `vehicle_armed` to flip
   to `True` in the telemetry stream.
4. Publish `joystick_x_pct` + `joystick_y_pct` at ~10 Hz on the
   source_id the mapping points at.
5. Call `arm(arm=false)` when you're done.

A minimal Python example using the Keelson SDK:

```python
import time, zenoh
from keelson import construct_pubsub_key, construct_rpc_key, enclose
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest, ArmAck, SetModeRequest, SetModeAck,
)
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis, ManualControlMapping, ManualControlMappingAck,
)


def _rpc(session, key, payload_bytes, timeout=5.0):
    """Tiny synchronous RPC helper. Production code should reuse a
    properly cached query handler."""
    replies = []
    session.get(key, lambda r: replies.append(r), payload=payload_bytes)
    deadline = time.time() + timeout
    while time.time() < deadline and not replies:
        time.sleep(0.05)
    if not replies:
        raise TimeoutError(key)
    return bytes(replies[0].ok.payload.to_bytes())


with zenoh.open(zenoh.Config()) as session:
    realm, entity, src = "rise", "motorboat-01", "shore-gcs"
    rpc = lambda proc: construct_rpc_key(realm, entity, proc, "mav/0")
    pub = lambda subj: construct_pubsub_key(realm, entity, subj, src)

    # 0) Wire stick / throttle to the existing joystick subjects.
    mapping = ManualControlMapping(axes={
        "steering": ManualControlAxis(subject="joystick_x_pct", source_id=src),
        "throttle": ManualControlAxis(subject="joystick_y_pct", source_id=src),
    })
    ManualControlMappingAck.FromString(
        _rpc(session, rpc("set_manual_control_mapping"),
             mapping.SerializeToString())
    )

    # 1) MANUAL mode
    req = SetModeRequest(mode="MANUAL"); req.timestamp.GetCurrentTime()
    SetModeAck.FromString(_rpc(session, rpc("set_mode"), req.SerializeToString()))
    time.sleep(1)

    # 2) Arm
    req = ArmRequest(arm=True); req.timestamp.GetCurrentTime()
    ArmAck.FromString(_rpc(session, rpc("arm"), req.SerializeToString()))
    time.sleep(1)

    # 3) Drive forward at half throttle for 5 seconds.
    steering_pub = session.declare_publisher(pub("joystick_x_pct"))
    throttle_pub = session.declare_publisher(pub("joystick_y_pct"))
    end = time.time() + 5
    while time.time() < end:
        s = TimestampedFloat(value=0.0); s.timestamp.GetCurrentTime()
        t = TimestampedFloat(value=50.0); t.timestamp.GetCurrentTime()
        steering_pub.put(enclose(s.SerializeToString()))
        throttle_pub.put(enclose(t.SerializeToString()))
        time.sleep(0.1)
    steering_pub.undeclare(); throttle_pub.undeclare()

    # 4) Disarm
    req = ArmRequest(arm=False); req.timestamp.GetCurrentTime()
    ArmAck.FromString(_rpc(session, rpc("arm"), req.SerializeToString()))
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

Switch to `GUIDED` mode, then call the `set_navigation_target` RPC:

```python
from keelson.interfaces.VehicleNavigation_pb2 import (
    NavigationTarget, NavigationTargetAck,
)
from keelson.interfaces.VehicleLifecycle_pb2 import SetModeRequest, SetModeAck

# 1) GUIDED mode first
req = SetModeRequest(mode="GUIDED"); req.timestamp.GetCurrentTime()
SetModeAck.FromString(_rpc(session, rpc("set_mode"), req.SerializeToString()))
time.sleep(1)

# 2) Send the target as an RPC call
key = construct_rpc_key("rise", "motorboat-01", "set_navigation_target", "shore-gcs")
target = NavigationTarget(latitude=59.351, longitude=18.071)
target.timestamp.GetCurrentTime()
# Issue zenoh `get` against the queryable; decode reply as NavigationTargetAck.
```

The autopilot navigates there and holds. Optional fields on
`NavigationTarget` let you also specify altitude, ground speed, and
target yaw. The RPC reply is a simple `NavigationTargetAck` (empty
success); rejections (wrong mode, fence violation, etc.) come back as
`ErrorResponse` on the error channel.

### Upload a pre-planned mission

Send a `mavlink.Mission` (list of `MissionItem` with `MAV_CMD_NAV_*`
commands) over the `upload_mission` RPC procedure. After upload, set
the vehicle to `AUTO` mode and arm — the autopilot executes the
mission. Pair with the `set_current_waypoint` RPC to jump mid-mission
and `clear_mission` to wipe it.

### Read and write autopilot parameters

Tune rates, PIDs, throttle caps, failsafe behaviour — anything in the
parameter list — from your scripts. `get_param`, `set_param`,
`list_params`, `set_params` are all RPC procedures under
`{realm}/@v0/{entity}/@rpc/<procedure>/{source_id}`.

```python
from keelson.interfaces.VehicleParam_pb2 import ParamGetRequest, ParamValueResponse
from keelson import construct_rpc_key

key = construct_rpc_key("rise", "motorboat-01", "get_param", "shore-gcs")
req = ParamGetRequest(name="MOT_THR_MAX")
# Issue the Zenoh get, decode the reply as ParamValueResponse.
```

On ArduPilot, `set_param` writes are persisted to storage immediately
and survive a reboot with no extra step. The `save_params` RPC is a
no-op on ArduPilot (it returns `DENIED`, which is expected) and exists
for PX4-class autopilots, which do not auto-persist.

### Constrain to a geofence

Upload a polygon or circular fence via `upload_geofence` RPC, then
call the `enable_geofence` RPC with `enabled=true`. The autopilot enforces the fence
according to `FENCE_ACTION` (`HOLD`, `RTL`, etc.). Always test the
fence on the bench by disconnecting the GCS link or driving toward the
fence at low throttle.

### Feed external sensors into the autopilot

Sensor injection is configured in a YAML file passed to the connector
via `--injection-config <path>`. The connector subscribes to the
existing telemetry subjects (`location_fix`, `location_fix_quality`, …) and
assembles MAVLink injection frames from them — the same subject can
carry "boat's reported GPS" on the uplink and "external GPS for the
autopilot to fuse" on the downlink, distinguished only by `source_id`.

**v1 supports only `GPS_INPUT`.** RTCM corrections, external pose /
attitude, distance sensor, battery status, system time, and body
velocity are deferred. They'll follow the same file format.

#### Minimal GPS-injection config

```yaml
# /etc/keelson/mavlink-injection.yaml
GPS_INPUT:
  sources:
    location_fix:                          "external-gnss/0"
    location_fix_quality:                  "external-gnss/0"
    location_fix_satellites_visible:       "external-gnss/0"
    location_fix_hdop:                     "external-gnss/0"
    speed_over_ground_knots:               "external-gnss/0"
    course_over_ground_deg:                "external-gnss/0"
  throttle_s: 0.2          # cap at 5 Hz
  max_companion_age_s: 1.0
```

Run the connector with the additional flag:

```bash
mavlink2keelson ... --injection-config /etc/keelson/mavlink-injection.yaml
```

Then publish to the listed subjects from your companion-side GPS
producer. Pair with `GPS_TYPE=14` on the autopilot for the fix to
actually be fused. See the README's "Downlink: sensor injection" section
for the full per-message format and per-field source contract.

#### Rate and timestamp matter

ArduPilot's EKF has rate floors and ceilings per sensor type. For
`GPS_INPUT` the band is 5–20 Hz. Below the floor the EKF starves and may
diverge or fall back to its default sources; above the ceiling you're
wasting bandwidth.

**The connector watches the trigger subject of each loaded mapping and
warns when rates drift.** A 5 s rolling window per mapping; transitions
to "below_floor", "silent", "above_ceiling", and "ok" each log once.
Add `--strict-rates` to turn floor-violation and silence transitions
into a fatal `RuntimeError` — useful for CI / pre-deploy validation
where you want a producer misconfiguration to fail loudly. Don't run
with `--strict-rates` in production: a single network hiccup will kill
the connector.

Each subject's `timestamp` field becomes the MAVLink `time_usec` on the
wire. ArduPilot will **reject** measurements that are too stale or too
far in the future:

- **Fill in the timestamp on the producer side**, with the time the
  sample was actually taken. Don't leave it zero — the connector falls
  back to wall-clock at forward time, which loses the sample instant
  and adds jitter.
- **Synchronise the producer's clock with the autopilot's.** Running
  NTP / chrony on the companion computer is the easy baseline.
- **Per-stream timestamps must be monotonic.** The connector trusts
  the producer; deduplicate / reorder upstream if your pipeline can
  emit out-of-order samples.

### Emergency stop and reboot

The `emergency_stop` RPC triggers `MAV_CMD_DO_FLIGHTTERMINATION`. The
autopilot disarms and stops driving outputs. Use sparingly.

There is no `reboot` RPC. To reboot or shut down the autopilot, use the
`send_command_long` escape hatch with `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN`
(command `246`, `param1=1` reboot / `2` shutdown / `3` bootloader). The
MAVLink link goes down with it; if you've run the connector under systemd
it'll restart and reconnect on its own. **On a BlueOS-based vehicle this
exits ArduPilot and BlueOS does not auto-relaunch it** — read "Rebooting
the autopilot" in `README.md` before using it.

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
   the full arm → joystick-driven → disarm sequence over Keelson, and
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
7. **Treat `emergency_stop` and autopilot reboot carefully.** Emergency
   stop is one-way — it disarms the vehicle and stops the autopilot's
   outputs. Rebooting the autopilot (via the `send_command_long` escape
   hatch) drops the MAVLink link entirely. Both are useful in genuine
   emergencies and during bring-up, but don't wire them into an autonomy
   loop without an explicit human guard.
8. **On ArduPilot, `set_param` writes persist immediately.** They
   survive a reboot with no separate save step — the `save_params` RPC
   is a no-op on ArduPilot and returns `DENIED` (expected, not a
   failure). To trial a tuning change without it sticking, record the
   old value first and restore it yourself.
9. **The Keelson command surface is unauthenticated.** Every RPC — arm,
   mode change, emergency stop, mission upload, parameter writes — is
   served with no authentication or authorization. Anyone who can reach
   the connector's `@rpc` keys over Zenoh can drive the boat. Keep the
   connector's Zenoh peer on a trusted network segment and do not bridge
   its `@rpc` keyspace onto a shared or cloud router without access
   control on that router. See the **Security** section of `README.md`.

---

## What this connector can and cannot do

### Can

- **Stream all the basic telemetry to Keelson**: position, attitude,
  mode, armed status, battery, speed, IMU, distance sensors.
- **Lifecycle**: arm / disarm, set mode, emergency stop. (Autopilot
  reboot is available via the `send_command_long` escape hatch.)
- **Drive**: stick-driving (joystick_*_pct subjects + VehicleControl RPC), point-to-point
  navigation (`set_navigation_target` RPC in GUIDED), AUTO-mode mission execution.
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
- Are you publishing the mapped joystick subjects fast enough? RC
  overrides expire after ~3 s; you want each mapped axis published at
  5–10 Hz minimum.
- Did you call `set_manual_control_mapping`? The connector subscribes
  to nothing by default — no mapping means no input reaches the
  autopilot.

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

**`list_params` / `download_mission` / `upload_mission` query times
out, but the connector logs show it completed.**

These RPCs do a multi-step MAVLink exchange and can take up to 30 s.
Zenoh's default query timeout (~10 s) is shorter than that, so the
client gives up while the handler is still running. Pass a longer
`timeout=` on the Zenoh query (35 s is safe for the mission and
list_params RPCs; `set_params` needs ~`5 + 2 * len(params)` s). See
the "Long-running RPCs" table in `README.md` for the per-procedure
worst case.

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
