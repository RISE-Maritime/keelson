# keelson-connector-mavlink

Bidirectional **MAVLink** (ArduPilot / PX4) ↔ **Keelson** connector.

**Uplink**: reads MAVLink frames over UDP, TCP, serial, or a TLog file and
republishes them as typed Keelson `Envelope`s on the standard telemetry
subjects (`vehicle_mode`, `location_fix`, `roll_deg`, …).

**Downlink**: subscribes to Keelson command, injection, and RPC subjects and
forwards them to the autopilot as the matching MAVLink messages — arming,
mode switching, stick-driving, GUIDED-mode `goto`, mission upload, geofence
upload, parameter get/set, sensor injection (GPS, RTK, depth, external
pose, battery, …), and a `send_command_long` escape hatch for anything not
yet typed.

Supersedes the `keelson-connector-blueos` + `blueos-gateway` chain on the
telemetry path by talking MAVLink directly via `pymavlink` — no BlueOS REST
hops, no JSON detour, full native message rate. Works against any MAVLink
source: a BlueROV behind BlueOS, an ArduPilot wired straight to a companion
Pi via USB, ArduPilot SITL, or a recorded TLog.

> **New to MAVLink or this connector?** Read
> [`GETTING-STARTED.md`](./GETTING-STARTED.md) first — it walks through
> the full setup (autopilot configuration, install, first telemetry,
> first command) and is the right entry point. This file is the
> reference for CLI flags, the full subject / RPC contract, and design
> rationale.

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
| `--steering-channel` | autodetect | RC channel to drive with `manual_control.steering`. Must match the autopilot's `RCMAP_ROLL`. Autodetected from the autopilot on first run; the cached value is reused on subsequent starts. |
| `--throttle-channel` | autodetect | RC channel to drive with `manual_control.throttle`. Must match the autopilot's `RCMAP_THROTTLE`. Autodetected on first run. |
| `--config-file` | `~/.keelson/mavlink-{entity_id}.json` | Per-vehicle cache file for the autodetected channel mapping (see "Channel autodetect" below). |
| `--strict-rates` | off | Turn `inject_*` rate-floor warnings and silent-producer warnings into a `RuntimeError` that exits the connector. Useful for CI / pre-deploy validation. See "Rate" in the sensor-injection section. |

### Channel autodetect

The first time the connector runs against a given vehicle, it:

1. Waits for the first `HEARTBEAT` from the autopilot.
2. Reads `RCMAP_ROLL`, `RCMAP_PITCH`, `RCMAP_THROTTLE`, `RCMAP_YAW`,
   `FRAME_CLASS`, `FRAME_TYPE`, and `SERVO1..16_FUNCTION` via
   `PARAM_REQUEST_READ` (re-requesting dropped responses every 2 s).
3. Computes a SHA-256 fingerprint over those values.
4. Writes `~/.keelson/mavlink-{entity_id}.json` containing the
   fingerprint, the detected steering/throttle channels, and the raw
   param values (for human inspection / diffing).

On subsequent runs, the connector reads the same params, recomputes
the fingerprint, and compares. If it matches, the cached channels are
used immediately. If it doesn't (e.g. someone changed `RCMAP_THROTTLE`
in Mission Planner, or via the `set_param` RPC), the file is rewritten
and the new mapping is used.

Override with `--steering-channel <N> --throttle-channel <N>` if you
want to skip autodetect entirely (e.g. for replay-only or
fixture-driven setups). Delete the config file to force a re-detect
on next start.

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
| `cmd_goto` | `mavlink.GoToCommand` | `SET_POSITION_TARGET_GLOBAL_INT` (GUIDED). Requires vehicle in GUIDED mode first. Optional `ground_speed_mps` triggers a separate `MAV_CMD_DO_CHANGE_SPEED`. |
| `cmd_set_cruise_speed` | `keelson.TimestampedFloat` (m/s) | `MAV_CMD_DO_CHANGE_SPEED` |
| `cmd_set_current_waypoint` | `keelson.TimestampedInt` (seq) | `MISSION_SET_CURRENT` |
| `cmd_emergency_stop` | `keelson.TimestampedBool` (true → terminate) | `MAV_CMD_DO_FLIGHTTERMINATION` |
| `cmd_enable_geofence` | `keelson.TimestampedBool` | `MAV_CMD_DO_FENCE_ENABLE` |
| `cmd_clear_mission` | `keelson.TimestampedBool` (true → clear) | `MISSION_CLEAR_ALL` |
| `cmd_save_params` | `keelson.TimestampedBool` (true → write) | `MAV_CMD_PREFLIGHT_STORAGE` |
| `cmd_reboot` | `mavlink.RebootCommand` (action enum) | `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` |

**Danger zone — `cmd_reboot`** disconnects the MAVLink link immediately and
the connector loop exits. Run under a supervisor (systemd, etc.) if you
want it to come back automatically.

## Downlink: sensor injection

These subjects feed external sensor measurements *into* the autopilot's
nav stack. Each requires matching autopilot configuration — listed in the
"prereq" column. The connector forwards 1:1 at whatever rate the producer
publishes.

| Subject | Keelson payload | MAVLink | Typical rate | Autopilot prereq |
| --- | --- | --- | --- | --- |
| `inject_gps` | `mavlink.GpsInjection` | `GPS_INPUT` | 5–10 Hz | `GPS_TYPE=14` (MAVLink GPS) |
| `inject_rtcm` | `keelson.TimestampedBytes` (RTCM3 frames) | `GPS_RTCM_DATA` (fragmented if > 180 B) | as base emits (~1 Hz of corrections) | RTK base + GPS that consumes RTCM |
| `inject_velocity_body_mps` | `keelson.Decomposed3DVector` | `VISION_SPEED_ESTIMATE` | 10–50 Hz | `EK3_SRC*_VELXY=6` (ExternalNav) |
| `inject_external_pose` | `mavlink.ExternalPoseInjection` | `VISION_POSITION_ESTIMATE` | 30–50 Hz (10 Hz floor) | `EK3_SRC*_POSXY=6` / `EK3_SRC*_POSZ=6` |
| `inject_external_attitude` | `mavlink.ExternalAttitudeInjection` | `ATT_POS_MOCAP` | 30–50 Hz | `EK3_SRC*_YAW=6` |
| `inject_distance_sensor` | `mavlink.DistanceSensorInjection` | `DISTANCE_SENSOR` | 10–50 Hz | `RNGFND*_TYPE=10` |
| `inject_battery_status` | `mavlink.BatteryStatusInjection` | `BATTERY_STATUS` | 1–10 Hz | `BATT_MONITOR=8` |
| `inject_system_time` | `keelson.TimestampedTimestamp` (value=UTC) | `SYSTEM_TIME` | 1 Hz | none |

The connector does *not* validate the autopilot's prereqs; it just forwards.
If your `inject_gps` doesn't seem to take effect, check `GPS_TYPE` first.

**Rate**: the connector forwards 1:1, so the publish rate on the Keelson
side is what the autopilot sees. The "typical rate" column is what
ArduPilot expects — drop below the floor and the EKF will start
starving the corresponding state estimate; go much higher than the
ceiling and you're wasting MAVLink bandwidth without changing the
fusion outcome.

The connector watches the arrival rate of each `inject_*` subject in a
5 s rolling window and reports deviations:

- Below the floor → `WARN`: *"X rate N.N Hz below floor F.F Hz — ArduPilot's
  EKF may starve on this signal"*.
- Above the ceiling → `INFO`: *"X rate N.N Hz exceeds ceiling C.C Hz
  (wasting bandwidth; not an error)"*.
- No samples for ≥ 15 s after the producer was previously alive →
  `WARN`: *"X has not produced a sample for N.N s — producer dead?"*.
- Back inside the band → `INFO`: *"X rate recovered to N.N Hz"*.

State transitions are reported once per episode, not per sample, so a
shaky producer doesn't spam the log. The first observation window is
3 s long; nothing is reported before then.

**`--strict-rates`** (off by default) turns the floor-violation and
silence transitions into a `RuntimeError` that kills the connector.
Useful for CI / pre-deploy validation where you want a noisy fail; not
recommended in production, where a single network hiccup would
otherwise take the connector down. The thresholds themselves live in
`INJECTION_RATE_LIMITS` at the top of `mavlink2keelson.py` — adjust
there if your application needs different bounds.

**Timestamps**: each injection's `timestamp` field (or the envelope's
own `enclosed_at` if the payload timestamp is unset) becomes the
MAVLink `time_usec` on the wire. ArduPilot's EKF will reject
measurements whose `time_usec` is too stale or too far in the future
relative to the autopilot's clock. Two implications:

- Producers should fill in `timestamp` rather than letting the connector
  fall back to wall-clock at forward time — it preserves the actual
  sample time of the sensor.
- The producer's clock should be reasonably synchronised with the
  autopilot's. Running NTP on the companion computer is the easy
  baseline; for tighter setups, publish `inject_system_time` at 1 Hz so
  the autopilot's `SYSTEM_TIME` tracks UTC, and the EKF's "too stale"
  tolerance buys you the rest.
- `time_usec` must be monotonic per sensor stream. The connector trusts
  the producer here; if your sensor pipeline can emit out-of-order
  timestamps, deduplicate / reorder upstream.

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

# End-to-end against ArduPilot SITL (requires sim_vehicle.py + ardurover
# on PATH — see .devcontainer/install-ardupilot-sitl.sh).
uv run pytest -vv -m e2e connectors/mavlink/
```

There are 8 e2e tests covering each pattern:

| Test | What it proves |
| --- | --- |
| `test_tlog_replay_publishes_expected_subjects` | Telemetry path against a recorded tlog (no SITL). |
| `test_sitl_telemetry_values` | Telemetry path against live SITL with sane decoded values. |
| `test_sitl_manual_control_drives_vehicle` | Full cmd_arm + cmd_set_mode + manual_control flow; the SITL Rover physically moves. |
| `test_sitl_get_param_returns_value` | `get_param` RPC against SITL. |
| `test_sitl_set_param_then_get_param_roundtrips` | `set_param` write + read-back; proves single-threaded MAVLink dispatch. |
| `test_sitl_inject_gps_forwards_without_crash` | Pub/sub injection path; verifies the connector decodes `GpsInjection` and emits `GPS_INPUT`. |
| `test_sitl_send_command_long_arms_vehicle` | Escape-hatch RPC end-to-end (issues `MAV_CMD_COMPONENT_ARM_DISARM`). |
| `test_sitl_mission_upload_download_roundtrips` | Pattern-C multi-step RPC: upload a 3-waypoint mission and download it. |

The SITL fixture (`_sitl_rover` in `tests/test_mavlink_e2e.py`) waits for
a HEARTBEAT before yielding the port, and `_wait_for_connector_ready`
subscribes to `vehicle_mode` and waits for the first envelope before the
test acts — so failures land with actionable error messages instead of
fixed-sleep flakiness.
