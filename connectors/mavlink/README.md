# keelson-connector-mavlink

Direct **MAVLink** (ArduPilot / PX4) ↔ **Keelson** connector. Reads MAVLink
messages over UDP, serial, or a TLog file and republishes them as typed
keelson `Envelope`s on well-known subjects.

Supersedes the `keelson-connector-blueos` + `blueos-gateway` chain by talking
MAVLink directly via `pymavlink` — no BlueOS REST hops, no JSON detour, full
native message rate. Works against any MAVLink source: a BlueROV behind
BlueOS, an ArduPilot wired straight to a companion Pi via USB, ArduPilot
SITL, or a recorded TLog.

## Binaries

| Binary | Direction | Status |
| --- | --- | --- |
| `mavlink2keelson` | Bidirectional: telemetry uplink + commands/injection/RPC downlink | shipped |

---

## Quick start

If you have ArduPilot SITL running on the same machine, you already have a
MAVLink source on UDP port 14550. Get telemetry onto the bus in three lines:

```bash
# 1) start a local zenoh router (in another terminal, or via docker)
docker run --rm -p 7447:7447 eclipse/zenoh:1.7.2

# 2) start the connector
mavlink2keelson \
  --realm test --entity-id sitl --source-id mav/0 \
  --mavlink-url udpin:0.0.0.0:14550 \
  --target-system 1 \
  --mode peer --connect tcp/localhost:7447

# 3) verify, in a third terminal
z_sub -k "test/@v0/sitl/pubsub/**"
```

You should see `vehicle_mode`, `vehicle_armed`, `entity_health`,
`location_fix`, `roll_deg`, … envelopes flowing.

---

## CLI reference

The connector takes three groups of arguments: identity (`--realm`,
`--entity-id`, `--source-id`), MAVLink (`--mavlink-url`, `--target-system`,
…), and Zenoh (`--mode`, `--connect`, `--listen`, `--log-level`).

The three you'll think hardest about are `--mavlink-url`, `--target-system`,
and `--connect`. They each answer a different question.

### `--mavlink-url`  *(where to read MAVLink from)*

Passed through to `pymavlink.mavutil.mavlink_connection()`. The connector
recognizes:

| Form | Meaning | When to use |
| --- | --- | --- |
| `udpin:HOST:PORT` | Bind a UDP socket and **listen** for inbound MAVLink frames | Most common. The vehicle (or BlueOS's `mavlink-router`, or SITL) sends to this address. `udpin:0.0.0.0:14550` listens on every interface on port 14550. |
| `udpout:HOST:PORT` | Initiate UDP — we send first, the vehicle replies | When the autopilot is configured as the *listener* and expects a GCS to dial in. |
| `tcp:HOST:PORT` | Open a TCP connection | Some SITL configurations. |
| `tlog:PATH` | Replay a `.tlog` file | Post-flight analysis, fixture-driven tests. The `tlog:` prefix is stripped before pymavlink autodetects the file format. |
| `/dev/ttyUSB0`, `/dev/ttyACM0`, `COM3` *(bare path, no scheme)* | Open a **serial port** | Direct USB/UART to a flight controller. Pair with `--baud` (default 57600). |

Common cases:

```bash
# BlueROV with BlueOS (mavlink-router forwards MAVLink to UDP 14550)
--mavlink-url udpin:0.0.0.0:14550

# ArduPilot wired directly to a companion Pi via USB
--mavlink-url /dev/ttyACM0 --baud 115200

# ArduPilot SITL (default output)
--mavlink-url udpin:0.0.0.0:14550

# Replay a captured flight
--mavlink-url tlog:flights/2026-05-14.tlog
```

### `--target-system`  *(which vehicle to listen to)*

MAVLink identifies every device on the bus with a `system_id` (1–255). A
vehicle is conventionally `1`; ground stations use `255` or another unused
value. **A single MAVLink network can carry traffic from multiple vehicles,
multiple GCSes, and companion computers all at once.**

`--target-system` says: *only process messages whose source `system_id`
matches this value.*

It's **required** (no default) because silently picking up the wrong
vehicle would publish bad telemetry under the wrong keelson `entity_id`.

Two related details:

- Messages with `srcSystem == 0` are also accepted — that's MAVLink's
  broadcast convention.
- `--target-component` (default `0` = "any component") narrows further if
  you also want to filter by which subsystem on the vehicle (autopilot,
  gimbal, companion computer, …) sent the message. For most vehicles you
  can leave it at the default; ArduPilot's autopilot is component `1`.

The flag pairs with `--entity-id`: **one MAVLink vehicle = one keelson
entity = one connector instance.** If you have two vehicles on the same
MAVLink network, run two connectors with different `--target-system` and
different `--entity-id`.

### `--connect`  *(where to put the resulting keelson messages)*

This is a **Zenoh-side** argument (it has nothing to do with MAVLink). It
tells the connector's embedded Zenoh peer where to find a Zenoh router or
another peer to connect to.

Format: `<transport>/<host>:<port>` — most commonly `tcp/HOST:PORT`. The
flag is `append`-style, so you can pass it multiple times for redundant
endpoints:

```bash
--connect tcp/localhost:7447                           # local zenoh router
--connect tcp/192.168.1.10:7447                        # router elsewhere on LAN
--connect tcp/r1:7447 --connect tcp/r2:7447            # two routers
```

Without `--connect`, Zenoh falls back to **multicast peer discovery** —
fine on a local LAN where multicast isn't blocked, but in production
you'll usually point at a known router.

Related Zenoh flags:

| Flag | Purpose |
| --- | --- |
| `--listen tcp/0.0.0.0:7447` | Advertise an endpoint other peers can dial *into* you on. |
| `--mode peer` | Full mesh participant (default for most setups). |
| `--mode client` | Router-attached only — lighter; can't talk to other peers directly. |

### Other arguments at a glance

| Flag | Default | Purpose |
| --- | --- | --- |
| `-r`, `--realm` | *required* | Keelson realm (top-level path segment, e.g. `rise`). |
| `-e`, `--entity-id` | *required* | Vehicle identifier within the realm (e.g. `ssrs18`). |
| `-s`, `--source-id` | *required* | This connector instance's source identifier (e.g. `mav/0`). Some subjects get a suffix appended automatically — e.g. GPS_RAW_INT publishes under `mav/0/gps_raw`. |
| `--baud` | `57600` | Serial baud rate. Only used when `--mavlink-url` is a bare device path. |
| `--source-system` | `254` | MAVLink `system_id` we send out as. Defaults to `254` so we don't collide with `blueos-gateway` (which uses `255`) during parallel-deploy migration. |
| `--source-component` | `MAV_COMP_ID_ONBOARD_COMPUTER` (191) | MAVLink `component_id` we send out as. |
| `--target-component` | `0` (any) | Filter incoming messages by source component. |
| `--recv-timeout` | `1.0` | Per-recv timeout in seconds. Controls how quickly the connector reacts to SIGINT. |
| `--log-level` | `20` (INFO) | Python log level (`10`=DEBUG, `20`=INFO, `30`=WARNING). |

---

## Putting it together — full examples

```bash
# (1) BlueROV-class vehicle running BlueOS
#     - BlueOS's mavlink-router forwards MAVLink to UDP 14550 on the companion
#     - one zenoh router on the topside laptop at 192.168.2.10:7447
mavlink2keelson \
  --realm rise --entity-id ssrs18 --source-id mav/0 \
  --mavlink-url udpin:0.0.0.0:14550 \
  --target-system 1 --target-component 1 \
  --mode peer --connect tcp/192.168.2.10:7447

# (2) USV with ArduPilot wired directly to a companion Pi via USB
#     - no BlueOS in the picture
mavlink2keelson \
  --realm rise --entity-id usv-alpha --source-id mav/0 \
  --mavlink-url /dev/ttyACM0 --baud 115200 \
  --target-system 1 \
  --mode peer --connect tcp/192.168.1.10:7447

# (3) ArduPilot SITL on a dev laptop, talking to a local zenoh router
mavlink2keelson \
  --realm test --entity-id sitl --source-id mav/0 \
  --mavlink-url udpin:0.0.0.0:14550 \
  --target-system 1 \
  --mode peer --connect tcp/localhost:7447

# (4) Replay a recorded flight against a peer
mavlink2keelson \
  --realm replay --entity-id drone-1 --source-id mav/0 \
  --mavlink-url tlog:flights/2026-05-14.tlog \
  --target-system 1 \
  --mode peer --connect tcp/192.168.1.10:7447
```

The mental model: **`--mavlink-url` says where to read MAVLink, `--target-system`
says which vehicle to pay attention to, `--connect` says where to put the
resulting keelson envelopes.**

---

## Subject contract

The connector publishes to the same subject names that
`keelson-connector-blueos` publishes today, so MCAP recordings, Foxglove
views, and autonomy stacks keep working unchanged. The full mapping table
lives at the top of `bin/mavlink2keelson.py` (search for
`MESSAGE_HANDLERS`). High-level summary:

| MAVLink message | Keelson subject(s) |
| --- | --- |
| `HEARTBEAT` | `vehicle_mode`, `vehicle_armed`, `entity_health` |
| `SYS_STATUS` | `entity_health` (per-sensor `CheckResult`s) |
| `GLOBAL_POSITION_INT` | `location_fix`, `altitude_above_msl_m`, `heading_true_north_deg`, `ned_velocity_mps` |
| `GPS_RAW_INT` | `location_fix` *(under `{source_id}/gps_raw`)*, `gps_fix_type`, `location_fix_satellites_visible`, `location_fix_hdop`, `location_fix_vdop`, `course_over_ground_deg` |
| `VFR_HUD` | `speed_over_ground_knots`, `climb_rate_mps`, `autopilot_throttle_pct` |
| `ATTITUDE` | `roll_deg`, `pitch_deg`, `yaw_deg`, `roll_rate_degps`, `pitch_rate_degps`, `yaw_rate_degps` |
| `ATTITUDE_QUATERNION` | `orientation_quaternion` |
| `LOCAL_POSITION_NED` | `surge_m`, `sway_m`, `heave_m` |
| `RAW_IMU` / `SCALED_IMU(2/3)` | `linear_acceleration_mpss`, `angular_velocity_radps`, `magnetic_field_gauss` |
| `BATTERY_STATUS` | `battery_voltage_v`, `battery_current_a`, `battery_state_of_charge_pct`, `battery_temperature_celsius` |

Anything not in the table is silently dropped (logged at `DEBUG`).

---

## Downlink: commands

These subjects accept enveloped payloads and forward them as MAVLink to the
autopilot. Existing endpoints (`cmd_arm`, `cmd_set_mode`, `manual_control`)
are documented separately in the GETTING-STARTED guide.

| Subject | Keelson payload | MAVLink result |
| --- | --- | --- |
| `cmd_goto` | `keelson.GoToCommand` | `SET_POSITION_TARGET_GLOBAL_INT` (GUIDED). Requires vehicle in GUIDED mode first. Optional `ground_speed_mps` triggers a separate `MAV_CMD_DO_CHANGE_SPEED`. |
| `cmd_set_cruise_speed` | `keelson.TimestampedFloat` (m/s) | `MAV_CMD_DO_CHANGE_SPEED` |
| `cmd_set_current_waypoint` | `keelson.TimestampedInt` (seq) | `MISSION_SET_CURRENT` |
| `cmd_emergency_stop` | `keelson.TimestampedBool` (true → terminate) | `MAV_CMD_DO_FLIGHTTERMINATION` |
| `cmd_enable_geofence` | `keelson.TimestampedBool` | `MAV_CMD_DO_FENCE_ENABLE` |
| `cmd_clear_mission` | `keelson.TimestampedBool` (true → clear) | `MISSION_CLEAR_ALL` |
| `cmd_save_params` | `keelson.TimestampedBool` (true → write) | `MAV_CMD_PREFLIGHT_STORAGE` |
| `cmd_reboot` | `keelson.RebootCommand` (action enum) | `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` |

**Danger zone — `cmd_reboot`** disconnects the MAVLink link immediately and
the connector loop exits. Run under a supervisor (systemd, etc.) if you
want it to come back automatically.

## Downlink: sensor injection

These subjects feed external sensor measurements *into* the autopilot's
nav stack. Each requires matching autopilot configuration — listed in the
"prereq" column. The connector forwards 1:1 at whatever rate the producer
publishes.

| Subject | Keelson payload | MAVLink | Autopilot prereq |
| --- | --- | --- | --- |
| `inject_gps` | `keelson.GpsInjection` | `GPS_INPUT` | `GPS_TYPE=14` (MAVLink GPS) |
| `inject_rtcm` | `keelson.TimestampedBytes` (RTCM3 frames) | `GPS_RTCM_DATA` (fragmented if > 180 B) | RTK base + GPS that consumes RTCM |
| `inject_velocity_body_mps` | `keelson.Decomposed3DVector` | `VISION_SPEED_ESTIMATE` | `EK3_SRC*_VELXY=6` (ExternalNav) |
| `inject_external_pose` | `keelson.ExternalPoseInjection` | `VISION_POSITION_ESTIMATE` | `EK3_SRC*_POSXY=6` / `EK3_SRC*_POSZ=6` |
| `inject_external_attitude` | `keelson.ExternalAttitudeInjection` | `ATT_POS_MOCAP` | `EK3_SRC*_YAW=6` |
| `inject_distance_sensor` | `keelson.DistanceSensorInjection` | `DISTANCE_SENSOR` | `RNGFND*_TYPE=10` |
| `inject_battery_status` | `keelson.BatteryStatusInjection` | `BATTERY_STATUS` | `BATT_MONITOR=8` |
| `inject_system_time` | `keelson.TimestampedTimestamp` (value=UTC) | `SYSTEM_TIME` | none |

The connector does *not* validate the autopilot's prereqs; it just forwards.
If your `inject_gps` doesn't seem to take effect, check `GPS_TYPE` first.

## Downlink: RPC

Request/response procedures, exposed as Zenoh queryables. Key shape:
`{realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}`. Error replies carry
an `interfaces.ErrorResponse` with a free-text description.

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `get_param` | `ParamGetRequest` → `ParamValueResponse` | Single param read; 2 s timeout. |
| `set_param` | `ParamSetRequest` → `ParamValueResponse` | Returns post-write echoed value. |
| `list_params` | `Empty` → `ParamListResponse` | Full PARAM_REQUEST_LIST stream; up to 30 s for a fully-tuned vehicle. |
| `set_params` | `ParamSetBulkRequest` → `ParamSetBulkResponse` | Bulk write; per-param result includes failures. |
| `upload_mission` | `Mission` → `MissionUploadResponse` | ArduPilot rewrites seq=0 with vehicle home — pad your missions accordingly. |
| `download_mission` | `Empty` → `Mission` | |
| `upload_geofence` | `Geofence` → `GeofenceUploadResponse` | Mission-protocol upload with `MAV_MISSION_TYPE_FENCE`. |
| `set_message_interval` | `SetMessageIntervalRequest` → `SetMessageIntervalResponse` | `hz=0` stops the message. |
| `send_command_long` | `CommandLongRequest` → `CommandLongResponse` | Escape hatch for any `COMMAND_LONG`. |

**Note on `set_param`** — writing `RCMAP_*` or `SERVOn_FUNCTION` invalidates
the channel-autodetect cache at `~/.keelson/mavlink-{entity_id}.json`. The
cache will be regenerated automatically on next connector restart (the
fingerprint mismatches and triggers re-detection).

---

## Migration from `keelson-connector-blueos`

The MAVLink connector is designed as a drop-in replacement for the existing
`blueos-gateway` + `keelson-connector-blueos` chain on the **telemetry
path**. Migration is parallel-run:

1. **Deploy side-by-side** with the existing chain. Use distinct
   `--source-id`s — e.g. `mav/0` vs the existing `blueos/0` — so both
   publish under the same subject names but separate Zenoh keys.
2. **Compare** in MCAP / Foxglove. Telemetry rate should jump from ~1 Hz
   (HTTP polling) to native rate (~5–10 Hz from ArduPilot).
3. **Cut over** vehicle-by-vehicle. Once parity is confirmed for a given
   vehicle, stop *both* `keelson-connector-blueos` and `blueos-gateway`
   for that vehicle — they retire as a pair (the gateway has no other
   consumers).

BlueOS itself is a separate decision: keep it running on a BlueROV for
video / network / Cockpit, and just point `--mavlink-url` at BlueOS's
`mavlink-router` UDP endpoint.

---

## Tests

```bash
# Unit + mapping tests (fast, no Zenoh, no network)
uv run pytest -vv -m "not e2e" connectors/mavlink/

# End-to-end (synthetic tlog -> Zenoh peer -> MCAP record)
uv run pytest -vv -m e2e connectors/mavlink/
```
