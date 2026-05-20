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
> Every typed-payload pub/sub command has been promoted to an RPC, and
> all connector-specific payload types have been removed. The connector
> now consumes only **existing** Keelson subjects on the bus —
> telemetry on the uplink, joystick / wheel / lever subjects for
> stick-driving, location_fix / gps_fix_type / … for injection.
>
> | Removed subject / type | Use instead |
> | --- | --- |
> | `cmd_goto` (pub/sub) | `set_navigation_target` RPC (`interfaces/VehicleNavigation.proto`) |
> | `cmd_set_cruise_speed` (pub/sub) | `set_cruise_speed` RPC (`VehicleNavigation`) |
> | `cmd_arm` (pub/sub) | `arm` RPC (`interfaces/VehicleLifecycle.proto`) |
> | `cmd_set_mode` (pub/sub) | `set_mode` RPC (`VehicleLifecycle`) |
> | `cmd_emergency_stop` (pub/sub) | `emergency_stop` RPC (`VehicleLifecycle`) |
> | `cmd_save_params` (pub/sub) | `save_params` RPC (`VehicleLifecycle`) |
> | `cmd_reboot` (pub/sub) | `reboot` RPC (`VehicleLifecycle`) |
> | `cmd_clear_mission` (pub/sub) | `clear_mission` RPC (`interfaces/VehicleMission.proto`) |
> | `cmd_set_current_waypoint` (pub/sub) | `set_current_waypoint` RPC (`VehicleMission`) |
> | `cmd_enable_geofence` (pub/sub) | `enable_geofence` RPC (`interfaces/VehicleGeofence.proto`) |
> | `manual_control` subject + `keelson.ManualControl` payload | Existing `joystick_x_pct` / `joystick_y_pct` / `wheel_position_pct` / etc., wired to MAVLink RC channels via the `VehicleControl.set_manual_control_mapping` RPC (`interfaces/VehicleControl.proto`) |
> | `cmd_active_source` / `active_command_source` (pub/sub) | Removed — never had a producer or consumer; declared aspirationally |
> | `inject_*` (8 subjects) | `--injection-config <yaml>` (see "Downlink: sensor injection") |
>
> Also renamed: `MavlinkParam` → `VehicleParam`, `MavlinkMission` →
> `VehicleMission`, `MavlinkGeofence` → `VehicleGeofence`. New interface
> file: `VehicleControl.proto` (per-axis manual_control mapping).
> `MavlinkCommand.proto` (the `send_command_long` escape hatch +
> `set_message_interval`) is intentionally kept MAVLink-shaped.
>
> `messages/payloads/mavlink/` and `messages/payloads/ManualControl.proto`
> are gone. Existing producers publishing to any of the dropped subjects
> need to migrate as per the table above.

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
| `--steering-channel` | autodetect | RC channel the `"steering"` manual-control axis drives. Must match the autopilot's `RCMAP_ROLL`. Autodetected from the autopilot on first run; the cached value is reused on subsequent starts. |
| `--throttle-channel` | autodetect | RC channel the `"throttle"` manual-control axis drives. Must match the autopilot's `RCMAP_THROTTLE`. Autodetected on first run. |
| `--config-file` | `${KEELSON_STATE_DIR:-~/.keelson}/mavlink-{entity_id}.json` | Per-vehicle cache file for the autodetected channel mapping (see "Channel autodetect" below). Set `KEELSON_STATE_DIR` to redirect the cache directory (recommended for container deployments — mount a volume and point the env var at it so the cache survives container restarts). |
| `--injection-config` | none | Path to a YAML injection-mapping file. Absent → no injection subscriptions; see "Downlink: sensor injection" below. |
| `--strict-rates` | off | Turn injection rate-floor warnings and silent-producer warnings into a `RuntimeError` that exits the connector. Useful for CI / pre-deploy validation. See "Rate monitoring" in the injection section. |

### Channel autodetect

The first time the connector runs against a given vehicle, it:

1. Waits for the first `HEARTBEAT` from the autopilot.
2. Reads `RCMAP_ROLL`, `RCMAP_PITCH`, `RCMAP_THROTTLE`, `RCMAP_YAW`,
   `FRAME_CLASS`, `FRAME_TYPE`, and `SERVO1..16_FUNCTION` via
   `PARAM_REQUEST_READ` (re-requesting dropped responses every 2 s).
3. Computes a SHA-256 fingerprint over those values.
4. Writes `${KEELSON_STATE_DIR:-~/.keelson}/mavlink-{entity_id}.json`
   containing the fingerprint, the detected steering/throttle channels,
   and the raw param values (for human inspection / diffing). In Docker
   set `KEELSON_STATE_DIR` to a mounted volume so the cache survives
   container restarts; otherwise the cache lives under `/root/.keelson`
   and disappears with the container.

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

## Zenoh interface — full specification

What follows is the complete contract between this connector and the
Zenoh bus. Everything the connector publishes, subscribes to, or serves
as an RPC queryable is enumerated here. The detail sections that follow
("Downlink: manual_control", "Downlink: sensor injection", "Downlink:
RPC") are the same surface with more context.

### Key shapes

Three Keelson key formats are used:

| Pattern | Format |
| --- | --- |
| Pub/sub | `{realm}/@v0/{entity_id}/pubsub/{subject}/{source_id}` |
| RPC | `{realm}/@v0/{entity_id}/@rpc/{procedure}/{responder_id}` |
| Liveliness | `{realm}/@v0/{entity_id}/pubsub/*/{source_id}` |

The connector substitutes:
- `{realm}` ← `--realm`
- `{entity_id}` ← `--entity-id`
- `{source_id}` / `{responder_id}` ← `--source-id` (the connector
  identifies itself with the same string for both pubsub publishes and
  RPC replies)

Subscribers can publish/query under any compatible key pattern; the
connector restricts what it consumes via the
`VehicleControl.set_manual_control_mapping` RPC (for stick-driving
axes) and `--injection-config` (for injection-driving subjects). See
the relevant sections for the scoping rules.

### Published — telemetry

The connector decodes 13 MAVLink message types and republishes them as
typed Keelson envelopes. Anything else MAVLink-side is dropped at
`DEBUG`.

Source-id convention: most subjects publish under the bare `--source-id`.
The one exception is the `location_fix` envelope derived from
`GPS_RAW_INT` (the lower-rate raw fix from the GPS receiver itself), which
publishes under `--source-id/gps_raw` to keep it distinguishable from
the `location_fix` derived from `GLOBAL_POSITION_INT` (the autopilot's
EKF-fused output).

| Subject | Payload type | Source MAVLink | Source-id suffix |
| --- | --- | --- | --- |
| `vehicle_mode` | `keelson.TimestampedString` | `HEARTBEAT` | — |
| `vehicle_armed` | `keelson.TimestampedBool` | `HEARTBEAT` | — |
| `entity_health` | `keelson.EntityHealth` | `HEARTBEAT`, `SYS_STATUS` | — |
| `location_fix` | `foxglove.LocationFix` | `GLOBAL_POSITION_INT` | — |
| `location_fix` | `foxglove.LocationFix` | `GPS_RAW_INT` | `/gps_raw` |
| `altitude_above_msl_m` | `keelson.TimestampedFloat` | `GLOBAL_POSITION_INT` | — |
| `heading_true_north_deg` | `keelson.TimestampedFloat` | `GLOBAL_POSITION_INT` | — |
| `ned_velocity_mps` | `keelson.Decomposed3DVector` | `GLOBAL_POSITION_INT` | — |
| `speed_over_ground_knots` | `keelson.TimestampedFloat` | `VFR_HUD` | — |
| `climb_rate_mps` | `keelson.TimestampedFloat` | `VFR_HUD` | — |
| `autopilot_throttle_pct` | `keelson.TimestampedFloat` | `VFR_HUD` | — |
| `gps_fix_type` | `keelson.TimestampedInt` | `GPS_RAW_INT` | — |
| `location_fix_satellites_visible` | `keelson.TimestampedInt` | `GPS_RAW_INT` | — |
| `location_fix_hdop` | `keelson.TimestampedFloat` | `GPS_RAW_INT` | — |
| `location_fix_vdop` | `keelson.TimestampedFloat` | `GPS_RAW_INT` | — |
| `course_over_ground_deg` | `keelson.TimestampedFloat` | `GPS_RAW_INT` | — |
| `roll_deg` / `pitch_deg` / `yaw_deg` | `keelson.TimestampedFloat` | `ATTITUDE` | — |
| `roll_rate_degps` / `pitch_rate_degps` / `yaw_rate_degps` | `keelson.TimestampedFloat` | `ATTITUDE` | — |
| `orientation_quaternion` | `keelson.TimestampedQuaternion` | `ATTITUDE_QUATERNION` | — |
| `surge_m` / `sway_m` / `heave_m` | `keelson.TimestampedFloat` | `LOCAL_POSITION_NED` | — |
| `linear_acceleration_mpss` | `keelson.Decomposed3DVector` | `RAW_IMU`, `SCALED_IMU`/2/3 | — |
| `angular_velocity_radps` | `keelson.Decomposed3DVector` | `RAW_IMU`, `SCALED_IMU`/2/3 | — |
| `magnetic_field_gauss` | `keelson.Decomposed3DVector` | `RAW_IMU`, `SCALED_IMU`/2/3 | — |
| `battery_voltage_v` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | — |
| `battery_current_a` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | — |
| `battery_state_of_charge_pct` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | — |
| `battery_temperature_celsius` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | — |

Every envelope is wrapped in `Envelope` (`enclosed_at` + serialized
inner payload) before publishing. Same contract as
`keelson-connector-blueos` — drop-in compatible on the telemetry path.

### Published — liveliness

The connector declares **one** liveliness token after receiving its first
valid `HEARTBEAT` from the autopilot. Key:

```
{realm}/@v0/{entity_id}/pubsub/*/{source_id}
```

The token is undeclared on clean shutdown. Treat its presence as "the
autopilot link is healthy and the connector is processing frames"; its
absence as either "the connector isn't running" or "no HEARTBEAT has
arrived since startup."

### Subscribed — pattern

The connector subscribes to two kinds of input on the bus: **stick /
throttle commands** (existing controller-input subjects like
`joystick_x_pct` and `joystick_y_pct`) and **external sensor
measurements** for the autopilot's EKF (existing telemetry subjects
like `location_fix`). Both follow the **same architectural pattern**:

- The *data path* is plain Keelson pub/sub on **existing** subjects.
  No connector-specific payload types, no new subjects invented for
  this connector.
- The *control plane* — "which Zenoh keys should I subscribe to?" — is
  declared by the operator and is the connector's interface. Different
  config mechanism per input (RPC for manual control; YAML file for
  injection), same conceptual shape.
- By default *no subscriptions are installed*. The connector consumes
  nothing on the bus until an operator wires it up.

Key pattern (per active source, regardless of which input):

```
{realm}/@v0/{configured_entity_id}/pubsub/{subject}/{configured_source_id_pattern}
```

`configured_entity_id` defaults to the connector's own `--entity-id`
when the operator's config doesn't specify one; pass an explicit
`entity_id` to subscribe under a different vehicle (the canonical
cross-entity case is RTCM corrections from a shore-side RTK base, even
though RTCM injection itself is deferred to v2).

A loopback guard rejects, at configuration time, any pattern that would
match the connector's *own* `--source-id` on its *own* `--entity-id` —
avoids feeding the autopilot's published telemetry back as an
"external" input.

#### Manual control (stick-driving)

| | |
| --- | --- |
| Subscribed subjects | One per mapped axis (v1 axes: `"steering"`, `"throttle"`). Typical picks: `joystick_x_pct` (steering), `joystick_y_pct` (throttle), `wheel_position_pct` (helm wheel), `lever_position_pct` (engine telegraph), `joystick_rt_pct` (trigger-as-accelerator). |
| Payload type | `keelson.TimestampedFloat` on every mapped subject (raw percent, see scaling below) |
| Expected rate | 5–20 Hz per axis (ArduPilot RC override expires after ~3 s of silence) |
| Per-frame action | One MAVLink `RC_CHANNELS_OVERRIDE` on every axis arrival, composed from the latest known value of each mapped axis. Unmapped axes contribute 0 (= "release override") to their RC channel. |
| Configuration mechanism | `VehicleControl.set_manual_control_mapping` RPC. Per-axis: `entity_id`, `subject`, `source_id`, `unipolar` flag (for `[0, 100]` sources), `invert` flag. Mapping also carries `min_interval_s` (output rate cap) + `max_axis_age_s` (staleness guard). No CLI flag — the operator must explicitly call the RPC after startup. |
| State holder | `ManualControlState` — owns the per-axis subscriber set + the last-known value per axis. `set_manual_control_mapping` atomically replaces the set. |

See "Downlink: manual_control" below for streaming semantics + safety
notes.

#### Sensor injection (autopilot-EKF inputs)

| | |
| --- | --- |
| Subscribed subjects | Existing telemetry subjects, declared per-mapping in the YAML config (v1: `location_fix`, `gps_fix_type`, `location_fix_satellites_visible`, etc. — see "Downlink: sensor injection" below) |
| Payload types | Existing Keelson types (`foxglove.LocationFix`, `keelson.TimestampedInt`, …) — the connector does not invent any |
| Expected rate | Per MAVLink output message; v1's `GPS_INPUT` expects 5–20 Hz on the trigger subject |
| Per-frame action | Assemble one MAVLink injection frame (v1: `GPS_INPUT`) from the trigger sample + the latest companion samples held in the skarv vault |
| Configuration mechanism | `--injection-config <path.yaml>` at startup (RPC reconfiguration deferred to v2) |
| State holder | Skarv vault — each subscribed subject's latest sample is held here; the connector's `@skarv.trigger` handler fires on the trigger subject and assembles the MAVLink frame |

See "Downlink: sensor injection" below for the YAML schema + GPS field
mapping.

### Queryable — RPC services

The connector declares one Zenoh queryable per procedure. Key:

```
{realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}
```

The `{source_id}` segment is the connector's `--source-id` (i.e. *this*
connector is the queryable's responder; clients address their query at
this exact key).

Success responses carry a typed protobuf payload (often an empty
`*Ack`). Failures are surfaced on the Zenoh error channel as
`interfaces.ErrorResponse` with a free-text `error_description`.

| Service | Procedure | Request → Response |
| --- | --- | --- |
| `VehicleNavigation` | `set_navigation_target` | `NavigationTarget` → `NavigationTargetAck` |
| `VehicleNavigation` | `set_cruise_speed` | `SetCruiseSpeedRequest` → `SetCruiseSpeedAck` |
| `VehicleLifecycle` | `arm` | `ArmRequest` → `ArmAck` |
| `VehicleLifecycle` | `set_mode` | `SetModeRequest` → `SetModeAck` |
| `VehicleLifecycle` | `emergency_stop` | `EmergencyStopRequest` → `EmergencyStopAck` |
| `VehicleLifecycle` | `save_params` | `SaveParamsRequest` → `SaveParamsAck` |
| `VehicleLifecycle` | `reboot` | `RebootRequest` → `RebootAck` |
| `VehicleMission` | `upload_mission` | `Mission` → `MissionUploadResponse` |
| `VehicleMission` | `download_mission` | `google.protobuf.Empty` → `Mission` |
| `VehicleMission` | `clear_mission` | `ClearMissionRequest` → `ClearMissionAck` |
| `VehicleMission` | `set_current_waypoint` | `SetCurrentWaypointRequest` → `SetCurrentWaypointAck` |
| `VehicleGeofence` | `upload_geofence` | `Geofence` → `GeofenceUploadResponse` |
| `VehicleGeofence` | `enable_geofence` | `EnableGeofenceRequest` → `EnableGeofenceAck` |
| `VehicleParam` | `get_param` | `ParamGetRequest` → `ParamValueResponse` |
| `VehicleParam` | `set_param` | `ParamSetRequest` → `ParamValueResponse` |
| `VehicleParam` | `list_params` | `google.protobuf.Empty` → `ParamListResponse` |
| `VehicleParam` | `set_params` | `ParamSetBulkRequest` → `ParamSetBulkResponse` |
| `VehicleControl` | `set_manual_control_mapping` | `ManualControlMapping` → `ManualControlMappingAck` |
| `VehicleControl` | `get_manual_control_mapping` | `google.protobuf.Empty` → `ManualControlMapping` |
| `MavlinkCommand` | `set_message_interval` | `SetMessageIntervalRequest` → `SetMessageIntervalResponse` |
| `MavlinkCommand` | `send_command_long` | `CommandLongRequest` → `CommandLongResponse` |

All `Vehicle*` services are vehicle-agnostic — defined so other
non-MAVLink connectors can implement the same shape. `MavlinkCommand`
is intentionally MAVLink-shaped (the `send_command_long` escape hatch).
Service definitions live at `interfaces/*.proto`; deeper notes per
procedure are in "Downlink: RPC" below.

---

## Downlink: manual_control

The connector's stick-driving input. The data flows on **existing**
Keelson controller-input subjects (`joystick_x_pct`, `joystick_y_pct`,
`wheel_position_pct`, `lever_position_pct`, `joystick_rt_pct`, …); the
connector's interface is the *per-axis mapping of which Zenoh keys
become which RC channel*, not the stream itself. Same pattern as
"Downlink: sensor injection" below — different configuration mechanism
(RPC vs. YAML file) for different reasons (manual control benefits
from live reconfiguration; injection sources rarely change at runtime).

### Stream semantics

Each axis is a separate subscription to a `keelson.TimestampedFloat`
subject. The connector buffers the latest value per axis. On *any*
axis arrival, the connector emits one MAVLink `RC_CHANNELS_OVERRIDE`
composed from the latest known values of every mapped axis (see
"Channel autodetect" above for how axis name → RC channel resolves).
ArduPilot's RC override expires after ~3 seconds of silence;
healthy stick-driving therefore publishes at 5–20 Hz continuously, and
"stop publishing" *is* how you stop driving.

### Recognized axes (v1)

| Axis name | RC channel resolved via |
| --- | --- |
| `"steering"` | autopilot's `RCMAP_ROLL` (`--steering-channel`) |
| `"throttle"` | autopilot's `RCMAP_THROTTLE` (`--throttle-channel`) |

Unknown axis names cause `set_manual_control_mapping` to reply with
`ErrorResponse` listing the recognized values. Future Plane / Copter
support would add `"roll"`, `"pitch"`, `"yaw"`.

### Value scaling

`TimestampedFloat.value` is interpreted by `ManualControlAxis.unipolar`:

| `unipolar` | Source range | Maps to | Notes |
| --- | --- | --- | --- |
| `false` (default) | `[-100, 100]` | `[-1.0, 1.0]`; raw=0 → neutral | Standard joystick / wheel / lever sources. |
| `true` | `[0, 100]` | `[0.0, 1.0]`; raw=0 → neutral, raw=100 → full forward | Trigger-style sources (`joystick_rt_pct`). Reverse is unreachable; use a bipolar source if you need it. |

The optional `invert` flag flips the sign after scaling — useful when
the producer's positive direction is opposite the autopilot's.

Unit values are then mapped to PWM `1500 + value*500` (so `-1.0 → 1000`,
`0.0 → 1500`, `+1.0 → 2000`). Unmapped axes contribute `0` to their RC
channel slot, which MAVLink interprets as "release this channel —
let the autopilot or physical RC keep it."

### Configuration

The connector does **not** subscribe to any axes by default. The only
way to install subscribers is the `VehicleControl.set_manual_control_mapping`
RPC. Calling it replaces the active mapping atomically:

```python
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis, ManualControlMapping,
)

mapping = ManualControlMapping(axes={
    "steering": ManualControlAxis(
        subject="joystick_x_pct", source_id="joystick-1",
    ),
    "throttle": ManualControlAxis(
        subject="joystick_y_pct", source_id="joystick-1",
    ),
}, min_interval_s=0.05, max_axis_age_s=0.5)
# Issue the Zenoh query on
#   {realm}/@v0/{entity_id}/@rpc/set_manual_control_mapping/{source_id}
# carrying mapping.SerializeToString().
```

`get_manual_control_mapping` returns the currently-installed mapping
(with `entity_id` normalized to the connector's own `--entity-id`
where it was originally empty).

#### Live reconfiguration

The same RPC handles joystick handoff and safety-pilot scenarios
without restarting the connector. Call `set_manual_control_mapping`
with a new mapping at any time; the old subscriber set is undeclared
in the same handler invocation before the new one is declared.

To **stop accepting input** entirely, call with an empty `axes` map.

#### Optional gates

| Field | Purpose |
| --- | --- |
| `min_interval_s` | Minimum interval between `RC_CHANNELS_OVERRIDE` emissions in seconds. Caps the wire rate when both axes update at the same producer cadence (default 0 = emit on every arrival). |
| `max_axis_age_s` | If any mapped axis's last sample is older than this (wall clock), skip the emission. Lets the autopilot's RC override stream go silent and trip its failsafe when a producer dies. Default 0 = no staleness check. |

## Downlink: commands — all RPC

Every typed-payload command is an RPC. See "Downlink: RPC" below for the
full table. The general shape is `{request: typed payload, response:
empty Ack}` — rejections surface as `ErrorResponse` on the RPC error
channel. Acks are deliberately empty: the autopilot's acceptance is the
only meaningful response, and follow-up state changes are visible on
the standard telemetry subjects (`vehicle_mode`, `vehicle_armed`, etc.).

## Downlink: sensor injection

The connector's external-measurement input for the autopilot's EKF
(companion-board GPS, RTK corrections, visual odometry pose, …). Same
pattern as "Downlink: manual_control" above — data flows on existing
pub/sub subjects, the connector's interface is the *configuration of
which Zenoh keys to subscribe to*. Different configuration mechanism
(YAML file rather than CLI + RPC) for a different reason: injection
mappings are deployment-static and rarely change at runtime; the file
makes them version-controllable alongside the rest of the deployment.

The connector subscribes to the existing telemetry subjects you'd
expect (`location_fix`, `gps_fix_type`, …) and assembles MAVLink
injection frames from them — so the same subject can carry "vehicle's
reported GPS" on the uplink and "external GPS for the autopilot to
fuse" on the downlink, distinguished only by source_id.

### Configuration

`--injection-config <path.yaml>` at startup. Absent → no injection
subscriptions installed. RPC reconfiguration is deferred to v2.

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

The interfaces named `Vehicle*` are vehicle-agnostic by intent — other
non-MAVLink connectors can implement the same service shape.
`MavlinkCommand` is the one intentionally-leaky exception, since
`send_command_long` is the "raw MAVLink" escape hatch.

### `VehicleNavigation`

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `set_navigation_target` | `NavigationTarget` → `NavigationTargetAck` | Point-to-point navigation (GUIDED-style modes). Maps onto MAVLink `SET_POSITION_TARGET_GLOBAL_INT`. Caller must put the vehicle in an appropriate mode first. |
| `set_cruise_speed` | `SetCruiseSpeedRequest` → `SetCruiseSpeedAck` | Change cruise / leg speed in m/s. Maps onto `MAV_CMD_DO_CHANGE_SPEED`. |

### `VehicleLifecycle`

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `arm` | `ArmRequest` → `ArmAck` | `arm=true/false`. Maps onto `MAV_CMD_COMPONENT_ARM_DISARM`. Arming pre-checks are enforced by the autopilot, not bypassed. |
| `set_mode` | `SetModeRequest` → `SetModeAck` | Symbolic mode name (e.g. `"MANUAL"`, `"GUIDED"`). Unknown modes return `ErrorResponse` with the list of known modes. |
| `emergency_stop` | `EmergencyStopRequest` → `EmergencyStopAck` | Calling the RPC at all is the signal — no `stop=false`. Maps onto `MAV_CMD_DO_FLIGHTTERMINATION`. |
| `save_params` | `SaveParamsRequest` → `SaveParamsAck` | Persists in-memory params to EEPROM. Maps onto `MAV_CMD_PREFLIGHT_STORAGE`. |
| `reboot` | `RebootRequest` → `RebootAck` | Action enum: `REBOOT` / `SHUTDOWN` / `REBOOT_TO_BOOTLOADER`. The MAVLink link goes down with the autopilot — run under a process supervisor if you want auto-reconnect. |

### `VehicleMission`

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `upload_mission` | `Mission` → `MissionUploadResponse` | ArduPilot rewrites seq=0 with vehicle home — pad your missions accordingly. |
| `download_mission` | `Empty` → `Mission` | |
| `clear_mission` | `ClearMissionRequest` → `ClearMissionAck` | Wipes the uploaded mission. Maps onto `MISSION_CLEAR_ALL`. |
| `set_current_waypoint` | `SetCurrentWaypointRequest` → `SetCurrentWaypointAck` | Jump to a specific waypoint seq. Maps onto `MISSION_SET_CURRENT`. |

### `VehicleGeofence`

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `upload_geofence` | `Geofence` → `GeofenceUploadResponse` | Mission-protocol upload with `MAV_MISSION_TYPE_FENCE`. |
| `enable_geofence` | `EnableGeofenceRequest` → `EnableGeofenceAck` | Enables / disables fence enforcement. Maps onto `MAV_CMD_DO_FENCE_ENABLE`. |

### `VehicleParam`

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `get_param` | `ParamGetRequest` → `ParamValueResponse` | Single param read; 2 s timeout. |
| `set_param` | `ParamSetRequest` → `ParamValueResponse` | Returns post-write echoed value. |
| `list_params` | `Empty` → `ParamListResponse` | Full PARAM_REQUEST_LIST stream; up to 30 s for a fully-tuned vehicle. |
| `set_params` | `ParamSetBulkRequest` → `ParamSetBulkResponse` | Bulk write; per-param result includes failures. |

### `VehicleControl`

| Procedure | Request → Response | Notes |
| --- | --- | --- |
| `set_manual_control_mapping` | `ManualControlMapping` → `ManualControlMappingAck` | Replace the active per-axis manual-control mapping atomically. Empty axes map = stop driving. Unknown axis names return `ErrorResponse`. |
| `get_manual_control_mapping` | `Empty` → `ManualControlMapping` | Inspect the currently-active mapping (with `entity_id` normalized to the connector's `--entity-id` where the operator left it blank). |

### `MavlinkCommand` (intentionally MAVLink-shaped)

| Procedure | Request → Response | Notes |
| --- | --- | --- |
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

There are 10 e2e tests covering each pattern:

| Test | What it proves |
| --- | --- |
| `test_tlog_replay_publishes_expected_subjects` | Telemetry path against a recorded tlog (no SITL). |
| `test_sitl_telemetry_values` | Telemetry path against live SITL with sane decoded values. |
| `test_sitl_manual_control_drives_vehicle` | Full RPC flow: `set_manual_control_mapping` (wiring joystick_x/y to steering/throttle), `set_mode("MANUAL")`, `arm(true)`, then publishing `joystick_*_pct` at 10 Hz with 70% throttle — the SITL Rover physically moves. |
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
