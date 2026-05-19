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

> **Breaking changes (in-progress branch `feature/mavlink-connector`).**
> The previous draft of this connector defined seven `mavlink.*` payload
> types and eight `inject_*` subjects on the Keelson bus. Those have all
> been removed:
>
> - `cmd_goto` (pub/sub) → `set_navigation_target` (RPC, generic
>   `NavigationTarget` payload in `interfaces/VehicleNavigation.proto`).
> - `cmd_reboot` (pub/sub) → `reboot` (RPC, generic `RebootRequest`
>   payload in `interfaces/VehicleLifecycle.proto`).
> - `inject_gps`, `inject_rtcm`, `inject_velocity_body_mps`,
>   `inject_external_pose`, `inject_external_attitude`,
>   `inject_distance_sensor`, `inject_battery_status`,
>   `inject_system_time` → file-driven injection (see "Downlink: sensor
>   injection" below). v1 supports only `GPS_INPUT`; the others are
>   deferred.
>
> The `messages/payloads/mavlink/` directory and the `mavlink` proto
> package are gone. Existing producers publishing to any of the dropped
> subjects need to migrate.

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
| `cmd_set_cruise_speed` | `keelson.TimestampedFloat` (m/s) | `MAV_CMD_DO_CHANGE_SPEED` |
| `cmd_set_current_waypoint` | `keelson.TimestampedInt` (seq) | `MISSION_SET_CURRENT` |
| `cmd_emergency_stop` | `keelson.TimestampedBool` (true → terminate) | `MAV_CMD_DO_FLIGHTTERMINATION` |
| `cmd_enable_geofence` | `keelson.TimestampedBool` | `MAV_CMD_DO_FENCE_ENABLE` |
| `cmd_clear_mission` | `keelson.TimestampedBool` (true → clear) | `MISSION_CLEAR_ALL` |
| `cmd_save_params` | `keelson.TimestampedBool` (true → write) | `MAV_CMD_PREFLIGHT_STORAGE` |

`goto` and `reboot` used to be pub/sub subjects too. They are now RPCs
(see "Downlink: RPC" below) — operations that need an Ack don't fit the
fire-and-forget pub/sub shape, and keeping typed payloads off the bus
avoids leaking MAVLink-specific shapes into the keelson namespace.

## Downlink: sensor injection (file-driven)

Sensor-injection mappings are declared in a YAML config file, not on the
bus. The connector subscribes to the existing telemetry subjects you'd
expect (`location_fix`, `gps_fix_type`, …) and assembles MAVLink
injection frames from them — so the same subject can carry "vehicle's
reported GPS" on the uplink and "external GPS for the autopilot to
fuse" on the downlink, distinguished only by source_id.

Configure with `--injection-config <path.yaml>`. Absent → no injection.

### v1: only `GPS_INPUT`

Seven other injection messages (RTCM, external pose / attitude, distance
sensor, battery status, system time, body-velocity) are deferred. They
will follow the same file format when added.

### File format

```yaml
GPS_INPUT:
  sources:
    # Trigger subject (hard-coded for GPS_INPUT). Required.
    location_fix:                          "external-gnss/0"

    # Required companions. If missing, the connector logs a warning at
    # startup and falls back to per-field defaults
    # (fix_type=3, satellites_visible=6).
    gps_fix_type:                          "external-gnss/0"
    location_fix_satellites_visible:       "external-gnss/0"

    # Optional companions. Absent → MAVLink ignore-bit set.
    location_fix_hdop:                     "external-gnss/0"
    location_fix_vdop:                     "external-gnss/0"
    location_fix_accuracy_horizontal_m:    "external-gnss/0"
    location_fix_accuracy_vertical_m:      "external-gnss/0"
    speed_over_ground_knots:               "external-gnss/0"
    course_over_ground_deg:                "external-gnss/0"
    climb_rate_mps:                        "external-gnss/0"

  throttle_s: 0.2          # cap emission at 5 Hz on the autopilot side
  max_companion_age_s: 1.0 # skip emit if any companion stale > 1 s
```

### Per-entry forms

Each value under `sources` is one of:

| Form | Means |
| --- | --- |
| `"<source_id_pattern>"` (string) | `entity_id` = connector's own `--entity-id`, `source_id` = the string |
| `{entity_id?: <str>, source_id: <str>}` (mapping) | `entity_id` defaults to the connector's own; `source_id` required |

Cross-entity inputs (the canonical case: RTCM from a shore-side RTK base)
use the long form; everything else is typically the short form.

### Validation (fail-fast at startup)

| Failure | Why |
| --- | --- |
| File doesn't exist | Operator opted in via `--injection-config` — silently disabling injection would be worse than crashing. |
| Top-level key isn't a supported MAVLink message | v1 only allows `GPS_INPUT`. Listed in the error. |
| `sources` key references an unknown Keelson subject | Not in `subjects.yaml`. |
| `sources` is missing the trigger subject | Trigger is required — no clock = no emission. |
| Source `source_id` would match the connector's own `--source-id` on the same `entity_id` | Loopback guard — would feed the autopilot its own published GPS back as an injection. |
| `throttle_s` / `max_companion_age_s` non-numeric / non-positive | Schema check. |

### Field mapping (hard-coded per MAVLink message)

For `GPS_INPUT`:

| MAVLink field | Sourced from |
| --- | --- |
| `lat` / `lon` / `alt` | `location_fix.latitude/longitude/altitude` |
| `fix_type` | `gps_fix_type.value` (default 3 = 3D fix) |
| `satellites_visible` | `location_fix_satellites_visible.value` (default 6) |
| `hdop` / `vdop` | `location_fix_hdop` / `location_fix_vdop` (ignore-bit if absent) |
| `horiz_accuracy` / `vert_accuracy` | `location_fix_accuracy_horizontal_m` / `..._vertical_m` (ignore-bit if absent) |
| `vn` / `ve` | Decomposed from `speed_over_ground_knots` + `course_over_ground_deg`. Either missing → vel-H ignore-bit set. |
| `vd` | `-climb_rate_mps` (positive-down convention). Absent → vel-V ignore-bit set. |
| `speed_accuracy` | No companion in v1 → always ignored. |
| `time_usec` | `location_fix.timestamp`. ArduPilot's EKF rejects samples whose `time_usec` is too stale or too far in the future, so producers should fill this in rather than relying on a fallback. |

### Autopilot prereqs

The connector forwards 1:1 but doesn't validate autopilot config. For
`GPS_INPUT` to actually be fused, set `GPS_TYPE=14` (MAVLink GPS) on the
autopilot. If `inject_gps` doesn't seem to take effect, check that first.

### Rate monitoring

The connector watches each mapping's *trigger subject* in a 5 s rolling
window and reports deviations from the per-MAVLink-message floor / ceiling
band (in `INJECTION_RATE_LIMITS` at the top of `mavlink2keelson.py`). For
`GPS_INPUT` the band is 5–20 Hz.

- Below the floor → `WARN`: *"X rate N.N Hz below floor F.F Hz — ArduPilot's
  EKF may starve on this signal"*.
- Above the ceiling → `INFO`: *"X rate N.N Hz exceeds ceiling C.C Hz
  (wasting bandwidth; not an error)"*.
- No samples for ≥ 15 s after the producer was previously alive →
  `WARN`: *"X has not produced a sample for N.N s — producer dead?"*.
- Back inside the band → `INFO`: *"X rate recovered to N.N Hz"*.

State transitions are reported once per episode, not per sample. First
observation window is 3 s; nothing is reported before then.

**`--strict-rates`** turns floor-violation and silence transitions into a
`RuntimeError` that kills the connector. Useful for CI / pre-deploy
validation; not recommended in production where a network hiccup would
otherwise take the connector down.

### Clock synchronisation

ArduPilot's EKF rejects measurements whose `time_usec` is too stale or
too far in the future relative to its own clock.

- Producers should fill in the `timestamp` field on each Envelope rather
  than letting the connector fall back to wall-clock at forward time.
- The producer's clock must be reasonably synchronised with the
  autopilot's. NTP / chrony on the companion computer is the easy
  baseline.
- `time_usec` must be monotonic per sensor stream — the connector trusts
  the producer. Deduplicate / reorder upstream if your pipeline can emit
  out-of-order samples.

## Downlink: RPC

Request/response procedures, exposed as Zenoh queryables. Key shape:
`{realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}`. Error replies carry
an `interfaces.ErrorResponse` with a free-text description.

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `set_navigation_target` | `NavigationTarget` → `NavigationTargetAck` | Point-to-point navigation (GUIDED-style modes). Maps onto MAVLink `SET_POSITION_TARGET_GLOBAL_INT`. Caller must put the vehicle in an appropriate mode first. |
| `reboot` | `RebootRequest` → `RebootAck` | Action enum: `REBOOT` / `SHUTDOWN` / `REBOOT_TO_BOOTLOADER`. Maps onto `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN`. The MAVLink link goes down with the autopilot — run under a process supervisor if you want auto-reconnect. |
| `get_param` | `ParamGetRequest` → `ParamValueResponse` | Single param read; 2 s timeout. |
| `set_param` | `ParamSetRequest` → `ParamValueResponse` | Returns post-write echoed value. |
| `list_params` | `Empty` → `ParamListResponse` | Full PARAM_REQUEST_LIST stream; up to 30 s for a fully-tuned vehicle. |
| `set_params` | `ParamSetBulkRequest` → `ParamSetBulkResponse` | Bulk write; per-param result includes failures. |
| `upload_mission` | `Mission` → `MissionUploadResponse` | ArduPilot rewrites seq=0 with vehicle home — pad your missions accordingly. |
| `download_mission` | `Empty` → `Mission` | |
| `upload_geofence` | `Geofence` → `GeofenceUploadResponse` | Mission-protocol upload with `MAV_MISSION_TYPE_FENCE`. |
| `set_message_interval` | `SetMessageIntervalRequest` → `SetMessageIntervalResponse` | `hz=0` stops the message. |
| `send_command_long` | `CommandLongRequest` → `CommandLongResponse` | Escape hatch for any `COMMAND_LONG`. |

`set_navigation_target` and `reboot` are defined in vehicle-agnostic
interfaces (`interfaces/VehicleNavigation.proto`, `interfaces/VehicleLifecycle.proto`)
so other vehicle connectors can implement the same service shape.

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

There are 10 e2e tests covering each pattern:

| Test | What it proves |
| --- | --- |
| `test_tlog_replay_publishes_expected_subjects` | Telemetry path against a recorded tlog (no SITL). |
| `test_sitl_telemetry_values` | Telemetry path against live SITL with sane decoded values. |
| `test_sitl_manual_control_drives_vehicle` | Full cmd_arm + cmd_set_mode + manual_control flow; the SITL Rover physically moves. |
| `test_sitl_get_param_returns_value` | `get_param` RPC against SITL. |
| `test_sitl_set_param_then_get_param_roundtrips` | `set_param` write + read-back; proves single-threaded MAVLink dispatch. |
| `test_sitl_gps_injection_via_injection_config` | File-driven injection: writes a YAML, publishes companion subjects from a separate source_id, asserts the connector survives the burst and keeps telemetry flowing. |
| `test_sitl_send_command_long_arms_vehicle` | Escape-hatch RPC end-to-end (issues `MAV_CMD_COMPONENT_ARM_DISARM`). |
| `test_sitl_mission_upload_download_roundtrips` | Pattern-C multi-step RPC: upload a 3-waypoint mission and download it. |
| `test_sitl_set_navigation_target_accepted` | `set_navigation_target` RPC against SITL: switches to GUIDED + arms, fires RPC, asserts the Ack returns cleanly. |
| `test_sitl_reboot_rpc_acked_and_drops_link` | `reboot` RPC against SITL: assertes the RPC ack arrives before the autopilot link drops. |

The SITL fixture (`_sitl_rover` in `tests/test_mavlink_e2e.py`) waits for
a HEARTBEAT before yielding the port, and `_wait_for_connector_ready`
subscribes to `vehicle_mode` and waits for the first envelope before the
test acts — so failures land with actionable error messages instead of
fixed-sleep flakiness.
