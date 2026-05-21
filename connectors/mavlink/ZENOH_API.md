# `mavlink2keelson` — Zenoh API reference

This is the complete contract between the `mavlink2keelson` connector and the
Zenoh bus. It enumerates every key the connector publishes, subscribes to, or
serves as an RPC queryable, plus the semantics of each. It is detailed enough
to hand to someone implementing a client (UI, autonomy stack, MCAP recorder,
test fixture) without their needing to read the connector source.

The companion [`README.md`](./README.md) covers operator-facing material — CLI
flags, install, migration. Both files reference the same proto definitions in
[`interfaces/`](../../interfaces/).

> **Versioning.** The connector is in active development on the
> `feature/mavlink-connector` branch. Interfaces under `Vehicle*` are
> intended to be stable across autopilot stacks; `MavlinkCommand` is
> intentionally MAVLink-shaped and may evolve as new escape-hatch needs
> surface. Pub/sub subject names follow the same contract as the
> superseded `keelson-connector-blueos` for drop-in compatibility.

---

## Table of contents

1. [Key shapes](#key-shapes)
2. [Connector configuration](#connector-configuration)
3. [Liveliness](#liveliness)
4. [Published telemetry (uplink)](#published-telemetry-uplink)
5. [Subscribed inputs (downlink)](#subscribed-inputs-downlink) — the shared
   architectural shape that manual_control and sensor injection both follow
6. [Manual control](#manual-control) — stick-driving via existing controller
   subjects
7. [Sensor injection](#sensor-injection) — external measurements for the
   autopilot's EKF
8. [RPC services](#rpc-services) — every typed-payload command
9. [Cross-entity inputs and loopback guard](#cross-entity-inputs-and-loopback-guard)
10. [End-to-end client examples](#end-to-end-client-examples)

---

## Key shapes

Three Keelson key formats are used, all rooted at `{realm}/@v0/{entity_id}`.

| Pattern | Format |
| --- | --- |
| Pub/sub | `{realm}/@v0/{entity_id}/pubsub/{subject}/{source_id}` |
| RPC | `{realm}/@v0/{entity_id}/@rpc/{procedure}/{responder_id}` |
| Liveliness | `{realm}/@v0/{entity_id}/pubsub/*/{source_id}` |

The connector substitutes:
- `{realm}` ← the connector's `--realm` flag
- `{entity_id}` ← the connector's `--entity-id` flag (one connector instance
  represents exactly one vehicle)
- `{source_id}` / `{responder_id}` ← the connector's `--source-id` flag (used
  identically for pub/sub publishes and RPC replies)

Clients address RPC calls and subscribe to telemetry using the exact key
formed from these three values. Clients publish or subscribe under their own
keys for the downlink data plane.

## Connector configuration

For the rest of this document:
- The connector publishes telemetry under its own `source_id` (with one
  documented suffix for raw GPS, below)
- The connector answers RPCs addressed at its own `source_id` segment
- The connector subscribes to the keys named in its
  `VehicleControl.set_manual_control_mapping` RPC (manual control) and its
  `--injection-config` YAML (sensor injection); these may be different
  `source_id`s — and even different `entity_id`s — from the connector's own,
  subject only to the loopback guard (see
  [§ Cross-entity inputs](#cross-entity-inputs-and-loopback-guard))

## Liveliness

The connector declares **one** liveliness token immediately after the Zenoh
session opens — before any MAVLink traffic has arrived — and undeclares it on
clean shutdown. Key:

```
{realm}/@v0/{entity_id}/pubsub/*/{source_id}
```

**Semantics: connector-alive, not vehicle-alive.** Treat the token's presence
as "the connector process is running and connected to Zenoh." Use the
freshness of [`entity_health`](#entity_health) (republished from every MAVLink
HEARTBEAT) for vehicle liveness. An aggregator rolling up health across
multiple sources should consume `entity_health` from this and similar
subjects, not the liveliness tokens.

---

## Published telemetry (uplink)

The connector decodes 13 MAVLink message types and republishes them as typed
Keelson envelopes. Anything not in the table below is dropped at DEBUG.

Every envelope is wrapped in `keelson.Envelope` (with `enclosed_at` set to
the connector's wall-clock time at the moment the MAVLink frame was parsed)
before publishing. Inner-payload types are referenced by name; see
[`messages/payloads/`](../../messages/payloads/) for the proto definitions.

**Source-id convention.** Most subjects publish under the connector's bare
`--source-id`. The single exception is the `location_fix` derived from
`GPS_RAW_INT` — the raw fix from the GPS receiver itself — which publishes
under `<--source-id>/gps_raw` to keep it distinguishable from the
`location_fix` derived from `GLOBAL_POSITION_INT` (the EKF-fused output).

| Subject | Payload type | Source MAVLink message | source_id |
| --- | --- | --- | --- |
| `vehicle_mode` | `keelson.TimestampedString` | `HEARTBEAT` | `--source-id` |
| `vehicle_armed` | `keelson.TimestampedBool` | `HEARTBEAT` | `--source-id` |
| <a id="entity_health">`entity_health`</a> | `keelson.EntityHealth` | `HEARTBEAT`, `SYS_STATUS` | `--source-id` |
| `location_fix` | `foxglove.LocationFix` | `GLOBAL_POSITION_INT` | `--source-id` |
| `location_fix` | `foxglove.LocationFix` | `GPS_RAW_INT` | `<--source-id>/gps_raw` |
| `altitude_above_msl_m` | `keelson.TimestampedFloat` | `GLOBAL_POSITION_INT` | `--source-id` |
| `heading_true_north_deg` | `keelson.TimestampedFloat` | `GLOBAL_POSITION_INT` | `--source-id` |
| `ned_velocity_mps` | `keelson.Decomposed3DVector` | `GLOBAL_POSITION_INT` | `--source-id` |
| `speed_over_ground_knots` | `keelson.TimestampedFloat` | `VFR_HUD` | `--source-id` |
| `climb_rate_mps` | `keelson.TimestampedFloat` | `VFR_HUD` | `--source-id` |
| `autopilot_throttle_pct` | `keelson.TimestampedFloat` | `VFR_HUD` | `--source-id` |
| `location_fix_quality` | `keelson.LocationFixQuality` | `GPS_RAW_INT` | `--source-id` |
| `location_fix_satellites_visible` | `keelson.TimestampedInt` | `GPS_RAW_INT` | `--source-id` |
| `location_fix_hdop` | `keelson.TimestampedFloat` | `GPS_RAW_INT` | `--source-id` |
| `location_fix_vdop` | `keelson.TimestampedFloat` | `GPS_RAW_INT` | `--source-id` |
| `course_over_ground_deg` | `keelson.TimestampedFloat` | `GPS_RAW_INT` | `--source-id` |
| `roll_deg` / `pitch_deg` / `yaw_deg` | `keelson.TimestampedFloat` | `ATTITUDE` | `--source-id` |
| `roll_rate_degps` / `pitch_rate_degps` / `yaw_rate_degps` | `keelson.TimestampedFloat` | `ATTITUDE` | `--source-id` |
| `orientation_quaternion` | `keelson.TimestampedQuaternion` | `ATTITUDE_QUATERNION` | `--source-id` |
| `surge_m` / `sway_m` / `heave_m` | `keelson.TimestampedFloat` | `LOCAL_POSITION_NED` | `--source-id` |
| `linear_acceleration_mpss` | `keelson.Decomposed3DVector` | `RAW_IMU`, `SCALED_IMU` (1/2/3) | `--source-id` |
| `angular_velocity_radps` | `keelson.Decomposed3DVector` | `RAW_IMU`, `SCALED_IMU` (1/2/3) | `--source-id` |
| `magnetic_field_gauss` | `keelson.Decomposed3DVector` | `RAW_IMU`, `SCALED_IMU` (1/2/3) | `--source-id` |
| `battery_voltage_v` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | `--source-id` |
| `battery_current_a` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | `--source-id` |
| `battery_state_of_charge_pct` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | `--source-id` |
| `battery_temperature_celsius` | `keelson.TimestampedFloat` | `BATTERY_STATUS` | `--source-id` |

### Rates

Telemetry rate is whatever the autopilot is configured to stream. Typical
ArduPilot defaults: HEARTBEAT 1 Hz; GPS/ATTITUDE/GLOBAL_POSITION_INT ~5–10 Hz;
IMU streams configurable. Use the `set_message_interval` RPC if a subject is
arriving slower than you need.

### Out-of-band subjects

`location_fix` appears twice — under the bare `source_id` (EKF-fused, from
`GLOBAL_POSITION_INT`) and under `<source_id>/gps_raw` (raw receiver fix,
from `GPS_RAW_INT`). They have different latency and reliability
characteristics; consumers usually want the EKF-fused one. The raw fix is
exposed for diagnostics and for the GPS-injection round-trip case.

---

## Subscribed inputs (downlink)

The connector consumes two kinds of input from the Zenoh bus: **stick /
throttle commands** (existing controller-input subjects like `joystick_x_pct`)
and **external sensor measurements** for the autopilot's EKF (existing
telemetry subjects like `location_fix`).

Both follow the **same architectural shape**:

- **Data plane**: plain Keelson pub/sub on existing subjects with existing
  payload types. The connector invents no new subjects and no new payload
  types for these inputs.
- **Control plane**: declared by the operator and is the connector's
  configuration interface. Different config mechanism per input (RPC for
  manual control because it benefits from live reconfiguration; YAML file for
  injection because mappings are deployment-static), same conceptual shape.
- **Default**: no subscriptions installed. The connector consumes nothing on
  the bus until an operator wires it up.

Key pattern (per active source, regardless of which input):

```
{realm}/@v0/{configured_entity_id}/pubsub/{subject}/{configured_source_id}
```

`{configured_entity_id}` defaults to the connector's own `--entity-id` when
the operator's config leaves it empty; an explicit value lets you subscribe
to a different vehicle's entity (see [§ Cross-entity inputs](#cross-entity-inputs-and-loopback-guard)).

---

## Manual control

Stick-driving via existing Keelson controller-input subjects. The data flows
on subjects like `joystick_x_pct`, `joystick_y_pct`, `wheel_position_pct`,
`lever_position_pct`, `joystick_rt_pct`; the connector's interface is the
*per-axis mapping of which Zenoh keys become which MAVLink RC channel*, not
the stream itself.

### Stream semantics

- Each axis is a separate subscription to a `keelson.TimestampedFloat`
  subject.
- The connector buffers the latest value per axis.
- On *any* axis arrival, the connector emits one MAVLink `RC_CHANNELS_OVERRIDE`
  composed from the latest known values of every mapped axis.
- ArduPilot's RC override expires after ~3 seconds of silence. Healthy
  stick-driving publishes continuously at 5–20 Hz. **"Stop publishing" *is*
  how you stop driving** — no explicit stop command exists or is needed.

### Recognised axes (v1)

| Axis name | Maps to autopilot's | Connector flag |
| --- | --- | --- |
| `"steering"` | `RCMAP_ROLL` | `--steering-channel` |
| `"throttle"` | `RCMAP_THROTTLE` | `--throttle-channel` |

Unknown axis names cause `set_manual_control_mapping` to reply with
`ErrorResponse` listing the recognised values. Future Plane / Copter support
would add `"roll"`, `"pitch"`, `"yaw"`.

### Value scaling

`TimestampedFloat.value` is interpreted per the `unipolar` flag on
`ManualControlAxis`:

| `unipolar` | Source range | Maps to | Notes |
| --- | --- | --- | --- |
| `false` (default) | `[-100, 100]` | `[-1.0, 1.0]`; raw=0 → neutral | Standard joystick / wheel / lever sources |
| `true` | `[0, 100]` | `[0.0, 1.0]`; raw=0 → neutral, raw=100 → full forward | Trigger-style sources (e.g. `joystick_rt_pct`); reverse is unreachable, use a bipolar source if you need it |

The optional `invert` flag flips the sign after scaling — useful when the
producer's positive direction is opposite the autopilot's.

Unit values are then mapped to PWM `1500 + value*500` (so `-1.0 → 1000`,
`0.0 → 1500`, `+1.0 → 2000`). Unmapped axes contribute `0` to their RC
channel slot, which MAVLink interprets as "release this channel — let the
autopilot or physical RC keep it."

### Configuration (RPC)

The connector subscribes to no axes by default. The only way to install
subscribers is the `VehicleControl.set_manual_control_mapping` RPC. Calling
it atomically replaces the active mapping.

```python
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis, ManualControlMapping,
)

mapping = ManualControlMapping(
    axes={
        "steering": ManualControlAxis(
            subject="joystick_x_pct", source_id="joystick-1",
        ),
        "throttle": ManualControlAxis(
            subject="joystick_y_pct", source_id="joystick-1",
        ),
    },
    min_interval_s=0.05,    # cap output rate at 20 Hz
    max_axis_age_s=0.5,     # skip emission if either axis stale > 0.5 s
)
# RPC key: {realm}/@v0/{entity_id}/@rpc/set_manual_control_mapping/{source_id}
```

To **stop accepting input** entirely, call with an empty `axes` map.

### Live reconfiguration

The same RPC handles joystick handoff and safety-pilot scenarios without
restarting the connector. Call `set_manual_control_mapping` with a new mapping
at any time; the old subscriber set is undeclared in the same handler
invocation before the new one is declared.

### Optional gates

| Field | Purpose |
| --- | --- |
| `min_interval_s` | Minimum interval between `RC_CHANNELS_OVERRIDE` emissions in seconds. Caps the wire rate when both axes update at the same producer cadence (default 0 = emit on every arrival). |
| `max_axis_age_s` | If any mapped axis's last sample is older than this (wall clock), skip the emission. Lets the autopilot's RC override stream go silent and trip its failsafe when a producer dies. Default 0 = no staleness check. |

---

## Sensor injection

External-measurement input for the autopilot's EKF — companion-board GPS,
RTK corrections (deferred to v2), visual odometry pose (deferred), … . Same
architectural shape as [manual control](#manual-control): data flows on
existing telemetry subjects, the connector's interface is the configuration
of which Zenoh keys to subscribe to. Different config mechanism (YAML file
rather than RPC) because injection mappings are deployment-static and benefit
from being version-controlled alongside the rest of the deployment.

The connector subscribes to the existing telemetry subjects you would expect
(`location_fix`, `location_fix_quality`, …) and assembles MAVLink injection frames
from them — so the same subject can carry "vehicle's reported GPS" on the
uplink and "external GPS for the autopilot to fuse" on the downlink,
distinguished only by `source_id`.

### Configuration (YAML file)

`--injection-config <path.yaml>` at startup. Absent → no injection
subscriptions are installed. RPC reconfiguration is deferred to v2.

### v1 scope: only `GPS_INPUT`

Seven other injection messages (RTCM, external pose / attitude, distance
sensor, battery status, system time, body-velocity) are deferred. They will
follow the same file format when added.

### File format

```yaml
GPS_INPUT:
  sources:
    # Trigger subject (hard-coded for GPS_INPUT). Required — the
    # connector composes one MAVLink frame per arrival on this subject.
    location_fix: "external-gnss/0"

    # Required companions. If missing, the connector logs a warning at
    # startup and falls back to per-field defaults
    # (fix_type = 3, satellites_visible = 6).
    location_fix_quality:                  "external-gnss/0"
    location_fix_satellites_visible:       "external-gnss/0"

    # Optional companions. Absent → corresponding MAVLink ignore-bit set.
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

### Per-entry source forms

Each value under `sources` is one of:

| Form | Means |
| --- | --- |
| `"<source_id>"` (string) | `entity_id` = connector's own `--entity-id`; `source_id` = the string |
| `{entity_id?: <str>, source_id: <str>}` (mapping) | `entity_id` defaults to the connector's own; `source_id` required |

Cross-entity inputs (the canonical case: RTCM from a shore-side RTK base)
use the long form; everything else is typically the short form.

### Startup validation

The connector fails fast on any of these — silently disabling injection would
be worse than crashing:

| Failure | Why |
| --- | --- |
| File doesn't exist | Operator opted in via `--injection-config` |
| Top-level key isn't a supported MAVLink message | v1 only allows `GPS_INPUT` |
| `sources` references an unknown Keelson subject | Not in `subjects.yaml` |
| `sources` is missing the trigger subject | No clock → no emission |
| A source `source_id` matches the connector's own `--source-id` on the same `entity_id` | [Loopback guard](#cross-entity-inputs-and-loopback-guard) |
| `throttle_s` / `max_companion_age_s` non-numeric / non-positive | Schema check |

### GPS_INPUT field mapping

| MAVLink field | Sourced from |
| --- | --- |
| `lat` / `lon` / `alt` | `location_fix.latitude/longitude/altitude` |
| `fix_type` | derived from `location_fix_quality` (RTK / fix status → MAVLink `GPS_FIX_TYPE`; default 3 = 3D fix) |
| `satellites_visible` | `location_fix_satellites_visible.value` (default 6) |
| `hdop` / `vdop` | `location_fix_hdop` / `location_fix_vdop` (ignore-bit if absent) |
| `horiz_accuracy` / `vert_accuracy` | `location_fix_accuracy_horizontal_m` / `..._vertical_m` (ignore-bit if absent) |
| `vn` / `ve` | Decomposed from `speed_over_ground_knots` + `course_over_ground_deg`. Either missing → vel-H ignore-bit set. |
| `vd` | `-climb_rate_mps` (positive-down convention). Absent → vel-V ignore-bit set. |
| `speed_accuracy` | No companion in v1 → always ignored. |
| `time_usec` | `location_fix.timestamp`. The autopilot rejects samples whose `time_usec` is too stale or too far in the future. |

### Autopilot prerequisites

The connector forwards 1:1 but doesn't validate autopilot config. For
`GPS_INPUT` to actually be fused, set `GPS_TYPE=14` (MAVLink GPS) on the
autopilot. If injection doesn't seem to take effect, check that first.

### Rate monitoring

The connector watches each mapping's trigger subject in a 5 s rolling window
and reports deviations from the per-MAVLink-message floor / ceiling band (see
`INJECTION_RATE_LIMITS` in `bin/mavlink2keelson.py`). For `GPS_INPUT` the
band is 5–20 Hz.

| Transition | Severity | Message shape |
| --- | --- | --- |
| Below the floor | WARN | "X rate N.N Hz below floor F.F Hz — ArduPilot's EKF may starve on this signal" |
| Above the ceiling | INFO | "X rate N.N Hz exceeds ceiling C.C Hz (wasting bandwidth; not an error)" |
| Silent ≥ 15 s after previously alive | WARN | "X has not produced a sample for N.N s — producer dead?" |
| Back inside the band | INFO | "X rate recovered to N.N Hz" |

State transitions are reported once per episode, not per sample. First
observation window is 3 s; nothing is reported before then.

**`--strict-rates`** turns floor-violation and silence transitions into a
`RuntimeError` that kills the connector. Useful for CI / pre-deploy
validation; not recommended in production where a transient network hiccup
would otherwise take the connector down.

### Clock synchronisation

ArduPilot's EKF rejects measurements whose `time_usec` is too stale or too
far in the future relative to its own clock.

- Producers should fill in the `timestamp` field on each Envelope rather than
  letting the connector fall back to wall-clock at forward time.
- The producer's clock must be reasonably synchronised with the autopilot's.
  NTP / chrony on the companion computer is the easy baseline.
- `time_usec` must be monotonic per sensor stream — the connector trusts the
  producer. Deduplicate / reorder upstream if your pipeline can emit
  out-of-order samples.

---

## RPC services

Every typed-payload command is a Zenoh queryable. Key shape:

```
{realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}
```

`{source_id}` is the connector's own — i.e. the connector is the queryable's
responder; clients address their query at this exact key.

### Consolidated response shape

Most RPC responses follow the same shape, defined in
[`interfaces/VehicleCommon.proto`](../../interfaces/VehicleCommon.proto):

```protobuf
CommandResult result = 1;          // see enum below
int32 raw_autopilot_result = 2;    // raw MAV_RESULT / MAV_MISSION_RESULT,
                                   // -1 if no ACK observed
string detail = 3;                 // free-text diagnostic
```

| `CommandResult` value | Meaning |
| --- | --- |
| `ACCEPTED` | Autopilot accepted and applied (or queued) the command. |
| `TEMPORARILY_REJECTED` | Autopilot rejected for now; retry may work. |
| `DENIED` | Autopilot rejected outright; retrying won't help. |
| `UNSUPPORTED` | Autopilot doesn't know this command. |
| `FAILED` | Autopilot tried and failed. |
| `IN_PROGRESS` | Command accepted; outcome pending. |
| `CANCELLED` | Autopilot cancelled an in-progress command. |
| `TIMEOUT` | No autopilot response within the handler's wait window — the connector observed nothing on the wire. |
| `NOT_OBSERVABLE` | Connector successfully sent the command on a MAVLink path that has no ACK (e.g. `SET_POSITION_TARGET_GLOBAL_INT`); the caller must infer success from downstream telemetry. |

`raw_autopilot_result` preserves the raw integer from the autopilot
(`MAV_RESULT` or `MAV_MISSION_RESULT`) so callers can disambiguate codes the
typed enum doesn't yet model. `detail` is populated for `TIMEOUT`,
`NOT_OBSERVABLE`, and where multiple commands fold into one response (e.g.
`set_navigation_target`'s optional `DO_CHANGE_SPEED`).

### Exceptions to the consolidated shape

| Procedure | Response | Why |
| --- | --- | --- |
| `set_manual_control_mapping` | `ManualControlMappingAck` | Connector-internal; no autopilot exchange. |
| `get_manual_control_mapping` | `ManualControlMapping` | Inspection RPC; returns the state. |
| `get_param` / `set_param` / `list_params` / `set_params` | `Param*Response` | Already richly informative — `value` + `mav_param_type` + per-item `ok/error` for bulk. |

### Transport-level failures

Errors that happen before the autopilot is involved — unreachable connector,
malformed request bytes, the (now removed) RPC queue having been full —
surface as `interfaces.ErrorResponse` on the Zenoh **error** channel, not
as a `CommandResult.FAILED`. Treat the two channels as distinct: "the
connector understood the request and asked the autopilot" → typed response;
"the connector couldn't process the request at all" → ErrorResponse.

### Full procedure table

| Service | Procedure | Request → Response |
| --- | --- | --- |
| `VehicleNavigation` | `set_navigation_target` | `NavigationTarget` → `NavigationTargetResponse` |
| `VehicleNavigation` | `set_cruise_speed` | `SetCruiseSpeedRequest` → `SetCruiseSpeedResponse` |
| `VehicleLifecycle` | `arm` | `ArmRequest` → `ArmResponse` |
| `VehicleLifecycle` | `set_mode` | `SetModeRequest` → `SetModeResponse` (adds `mode_actual`) |
| `VehicleLifecycle` | `emergency_stop` | `EmergencyStopRequest` → `EmergencyStopResponse` |
| `VehicleLifecycle` | `save_params` | `SaveParamsRequest` → `SaveParamsResponse` |
| `VehicleMission` | `upload_mission` | `Mission` → `MissionUploadResponse` |
| `VehicleMission` | `download_mission` | `google.protobuf.Empty` → `Mission` |
| `VehicleMission` | `clear_mission` | `ClearMissionRequest` → `ClearMissionResponse` |
| `VehicleMission` | `set_current_waypoint` | `SetCurrentWaypointRequest` → `SetCurrentWaypointResponse` (adds `seq_actual`) |
| `VehicleGeofence` | `upload_geofence` | `Geofence` → `GeofenceUploadResponse` |
| `VehicleGeofence` | `enable_geofence` | `EnableGeofenceRequest` → `EnableGeofenceResponse` |
| `VehicleParam` | `get_param` | `ParamGetRequest` → `ParamValueResponse` |
| `VehicleParam` | `set_param` | `ParamSetRequest` → `ParamValueResponse` |
| `VehicleParam` | `list_params` | `google.protobuf.Empty` → `ParamListResponse` |
| `VehicleParam` | `set_params` | `ParamSetBulkRequest` → `ParamSetBulkResponse` |
| `VehicleControl` | `set_manual_control_mapping` | `ManualControlMapping` → `ManualControlMappingAck` |
| `VehicleControl` | `get_manual_control_mapping` | `google.protobuf.Empty` → `ManualControlMapping` |
| `MavlinkCommand` | `set_message_interval` | `SetMessageIntervalRequest` → `SetMessageIntervalResponse` |
| `MavlinkCommand` | `send_command_long` | `CommandLongRequest` → `CommandLongResponse` |

All `Vehicle*` services are defined as vehicle-agnostic — by intent so that
other non-MAVLink connectors (a future ROS bridge, a proprietary autopilot
integration) can implement the same RPC contract. `MavlinkCommand` is the
exception: `send_command_long` is the "raw MAVLink" escape hatch and is
intentionally MAVLink-shaped.

Proto definitions: [`interfaces/Vehicle*.proto`](../../interfaces/) and
[`interfaces/MavlinkCommand.proto`](../../interfaces/MavlinkCommand.proto).

### Long-running RPCs — client-side timeout

Most RPCs reply in well under a second. Four of them do multi-step MAVLink
exchanges with the autopilot and can exceed Zenoh's default query timeout
(~10 s). Configure the client-side `timeout` to match, otherwise the caller
gives up before the response arrives — even though the connector is still
working.

| Procedure | Worst-case latency | Recommended client `timeout` (seconds) |
| --- | --- | --- |
| `list_params` | ~30 s (autopilot streams the entire param table) | 35 |
| `upload_mission` | ~30 s + per-item ack | 35 |
| `download_mission` | ~30 s + per-item request/reply | 35 |
| `set_params` | ~2 s per param × N | `5 + 2*N` |

The values above are the handler's own internal deadlines; the client should
add a small margin. All other procedures complete in single-digit seconds
and are safe at the Zenoh default.

### Concurrency

Each Zenoh queryable runs its callback on its own dedicated Zenoh callback
thread. Different procedures (e.g. `arm` and `list_params`) can therefore
run concurrently — a long-running `list_params` does not block an `arm`.

Two simultaneous calls on the *same* procedure serialise on that procedure's
callback thread. This is usually what you want (e.g. you don't want two
concurrent `set_mode` calls racing) but means a back-pressured caller of one
procedure does not affect others.

Telemetry publishes from a separate recv thread that owns the MAVLink
socket; RPC handlers cannot stall telemetry.

### Per-service notes

#### `VehicleNavigation`

- **`set_navigation_target`** — point-to-point navigation (GUIDED-style
  modes). Maps onto MAVLink `SET_POSITION_TARGET_GLOBAL_INT`. This MAVLink
  message has *no* ACK and ArduPilot drops invalid targets silently, so the
  connector polls `POSITION_TARGET_GLOBAL_INT` briefly to confirm the
  autopilot's commanded target matches what we sent. If that stream isn't
  running (default ArduPilot stream rates don't include it) the result is
  `NOT_OBSERVABLE` — call `set_message_interval` for
  `POSITION_TARGET_GLOBAL_INT` first to enable observability. The caller is
  responsible for putting the vehicle in an appropriate mode first.
- **`set_cruise_speed`** — change cruise / leg speed in m/s. Maps onto
  `MAV_CMD_DO_CHANGE_SPEED`. ArduPilot Rover defers to the active mode's
  `set_desired_speed` method; expect `FAILED` if the vehicle isn't in a
  mode with an active speed target.

#### `VehicleLifecycle`

- **`arm`** — `arm=true/false`. Maps onto `MAV_CMD_COMPONENT_ARM_DISARM`.
  Arming pre-checks are enforced by the autopilot, not bypassed.
- **`set_mode`** — symbolic mode name (e.g. `"MANUAL"`, `"GUIDED"`,
  `"AUTO"`). Maps onto `MAV_CMD_DO_SET_MODE` via the `COMMAND_LONG` path
  (which gets us a `COMMAND_ACK`, unlike the legacy `SET_MODE` message).
  Unknown modes return `ErrorResponse` with the list of known modes. The
  response includes `mode_actual`, polled from the next `HEARTBEAT`
  post-ACK.
- **`emergency_stop`** — calling the RPC at all is the signal; there is no
  `stop=false`. Maps onto `MAV_CMD_DO_FLIGHTTERMINATION`.
- **`save_params`** — persists in-memory params to non-volatile storage.
  Maps onto `MAV_CMD_PREFLIGHT_STORAGE`. **No-op on ArduPilot:** ArduPilot
  persists every `set_param` write to storage immediately, so the bulk
  storage command is redundant and the autopilot correctly returns
  `DENIED` — `result = DENIED` here is the expected outcome, not a
  failure. The RPC is meaningful for PX4-class autopilots, which do not
  auto-persist. (Pre-4.x ArduPilot firmware returns `ACCEPTED`.)
There is **no `reboot` RPC**. Rebooting or shutting down the autopilot is
done through `MavlinkCommand.send_command_long` with
`MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` (command `246`; `param1` = `1` reboot
/ `2` shutdown / `3` reboot-to-bootloader). `TIMEOUT` is the expected
`result` — the autopilot drops the link before its `COMMAND_ACK`, and
reconnection telemetry is the real success signal. **On BlueOS /
Navigator the autopilot is not auto-relaunched after this command; even
"reboot" behaves as a shutdown.** See "Rebooting the autopilot" in
[`README.md`](./README.md).

#### `VehicleMission`

`MissionItem` is a typed `oneof step` of `Waypoint` / `Loiter` / `Delay` /
`ReturnHome` / `ChangeSpeed` / `SetHome` — see
[`VehicleMission.proto`](../../interfaces/VehicleMission.proto). Sequence
position is the index in `Mission.items` (no separate `seq` field).

- **`upload_mission`** — ArduPilot rewrites seq=0 with the vehicle's home
  position on upload, so the first uploaded waypoint typically doesn't
  survive round-trip. `result` is mapped from `MAV_MISSION_RESULT`; the
  original code is preserved in `raw_autopilot_result`.
- **`download_mission`** — request body is empty. Unknown `MAV_CMD`s in the
  downloaded mission cause the response to be an `ErrorResponse` rather
  than a partial Mission — the connector only round-trips step types it
  can map.
- **`clear_mission`** — wipes the uploaded mission. Maps onto
  `MISSION_CLEAR_ALL`; ACK comes back as `MISSION_ACK`.
- **`set_current_waypoint`** — jump to a specific waypoint `seq`. Maps onto
  `MISSION_SET_CURRENT` (which has no `COMMAND_ACK`); the connector
  observes the next `MISSION_CURRENT` and reports `seq_actual` in the
  response.

#### `VehicleGeofence`

`Geofence` is a list of `FenceZone`s (each `INCLUSION` or `EXCLUSION` with a
`oneof shape` of `Polygon` or `Circle`) plus a singular `return_point` — see
[`VehicleGeofence.proto`](../../interfaces/VehicleGeofence.proto).

- **`upload_geofence`** — mission-protocol upload with
  `MAV_MISSION_TYPE_FENCE`. The connector fans each `Polygon` into N
  consecutive MAVLink fence items (N = vertex count) sharing the same
  `command` (`NAV_FENCE_POLYGON_VERTEX_{INCLUSION,EXCLUSION}`) with
  `param1 = vertex_count`. Same `result` / `raw_autopilot_result` semantics
  as `upload_mission`. ArduPilot may need `FENCE_TYPE != 0` set via
  `set_param` first.
- **`enable_geofence`** — enables / disables fence enforcement. Maps onto
  `MAV_CMD_DO_FENCE_ENABLE`. The `FENCE_ACTION` parameter controls what the
  vehicle does on breach (HOLD, RTL, etc.) and is separate from this RPC.

#### `VehicleParam`

- **`get_param`** — single param read; 2 s timeout. Returns the post-read
  echoed value + `mav_param_type` (the autopilot's internal type, e.g. 9
  for REAL32). **Read-after-write staleness:** a `get_param` issued
  immediately after a `set_param` for the same parameter can race the
  autopilot's apply and return the pre-write value — the connector reads
  live from the autopilot and does not cache the confirmed write. For
  read-after-write consistency, use the echoed value already returned in
  the `set_param` response, or retry the `get_param` after a short delay.
- **`set_param`** — single param write; returns the post-write echoed value
  so callers can detect autopilot-side type coercion. Note: writing
  `RCMAP_*` or `SERVOn_FUNCTION` invalidates the connector's channel
  autodetect cache, which is regenerated on the next connector restart.
- **`list_params`** — full `PARAM_REQUEST_LIST` stream. Up to 30 s for a
  fully-tuned vehicle. Telemetry continues during the call (each procedure
  has its own callback thread).
- **`set_params`** — bulk write. Per-param result includes failures; one
  failing param doesn't abort the rest.

#### `VehicleControl`

- **`set_manual_control_mapping`** — replace the active per-axis manual
  control mapping atomically. Empty `axes` map = stop driving. Unknown
  axis names return `ErrorResponse`. See [§ Manual control](#manual-control)
  for the data-plane semantics.
- **`get_manual_control_mapping`** — inspect the currently-active mapping.
  `entity_id` is normalised to the connector's `--entity-id` where the
  operator left it blank.

#### `MavlinkCommand` (intentionally MAVLink-shaped)

- **`set_message_interval`** — set the rate at which a specific MAVLink
  message is streamed. `hz=0` stops the message. Useful for enabling
  `POSITION_TARGET_GLOBAL_INT` before `set_navigation_target` (so that RPC
  can observe its commanded target).
- **`send_command_long`** — escape hatch for any `COMMAND_LONG`. Prefer the
  typed `Vehicle*` RPCs where they exist; reach for this only when you need
  a MAV_CMD that has no typed wrapper yet — for example rebooting the
  autopilot (`MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN`, command `246`; see the
  `VehicleLifecycle` notes above).

---

## Cross-entity inputs and loopback guard

Both manual control and sensor injection can subscribe to subjects under
**any** `entity_id`, not just the connector's own. The canonical
cross-entity case is RTCM corrections from a shore-side RTK base entity
flowing into multiple vehicle connectors (RTCM injection itself is deferred
to v2, but the architectural shape supports it).

When the operator's config (`ManualControlAxis.entity_id` or the
per-source mapping form in the YAML) leaves `entity_id` blank, the connector
substitutes its own `--entity-id`. An explicit non-empty `entity_id`
overrides this and lets you subscribe across vehicle entities.

### Loopback guard

The connector refuses, at configuration time, any subscription pattern that
would match its **own** `--source-id` on its **own** `--entity-id`. This
prevents the autopilot's published telemetry from being fed back to it as an
"external" input — a particularly easy mistake to make for GPS injection
where the inbound and outbound `location_fix` subjects share a name.

The guard is fail-fast: invalid configs raise on startup (for YAML) or
return `ErrorResponse` (for the manual-control RPC).

---

## End-to-end client examples

### Subscribe to live telemetry

```python
import zenoh
from keelson.payloads.Primitives_pb2 import TimestampedString
from keelson import uncover

session = zenoh.open(zenoh.Config())

def on_sample(sample):
    payload, _enclosed_at = uncover(sample.payload.to_bytes())
    mode = TimestampedString.FromString(payload).value
    print(f"vehicle_mode = {mode}")

session.declare_subscriber("rise/@v0/ssrs18/pubsub/vehicle_mode/mav/0", on_sample)
```

### Arm + switch to GUIDED + send a goto target

```python
from keelson import construct_rpc_key
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmRequest, SetModeRequest,
)
from keelson.interfaces.VehicleNavigation_pb2 import (
    NavigationTarget, NavigationTargetResponse,
)
from keelson.interfaces.VehicleCommon_pb2 import CommandResult

def rpc(session, key, request_bytes, response_cls, timeout=10.0):
    replies = []
    session.get(key, lambda r: replies.append(r), payload=request_bytes,
                timeout=zenoh.Duration(seconds=timeout))
    # ... wait for reply (synchronously or via a Future) ...
    resp = response_cls()
    resp.ParseFromString(bytes(replies[0].ok.payload.to_bytes()))
    return resp

# 1) Switch to GUIDED
mode_key = construct_rpc_key("rise", "ssrs18", "set_mode", "mav/0")
rpc(session, mode_key,
    SetModeRequest(mode="GUIDED").SerializeToString(),
    response_cls=...)  # SetModeResponse

# 2) Arm
arm_key = construct_rpc_key("rise", "ssrs18", "arm", "mav/0")
rpc(session, arm_key,
    ArmRequest(arm=True).SerializeToString(),
    response_cls=...)

# 3) Goto
target_key = construct_rpc_key("rise", "ssrs18", "set_navigation_target", "mav/0")
target = NavigationTarget(latitude=59.351, longitude=18.071)
resp = rpc(session, target_key, target.SerializeToString(),
           response_cls=NavigationTargetResponse)
assert resp.result == CommandResult.COMMAND_RESULT_ACCEPTED
```

### Wire a joystick to stick-driving

```python
from keelson import construct_pubsub_key, construct_rpc_key, enclose
from keelson.interfaces.VehicleControl_pb2 import (
    ManualControlAxis, ManualControlMapping, ManualControlMappingAck,
)
from keelson.payloads.Primitives_pb2 import TimestampedFloat

# 1) Tell the connector which subjects drive which axis.
mapping = ManualControlMapping(
    axes={
        "steering": ManualControlAxis(
            subject="joystick_x_pct", source_id="joystick-1",
        ),
        "throttle": ManualControlAxis(
            subject="joystick_y_pct", source_id="joystick-1",
        ),
    },
    min_interval_s=0.05,
)
rpc(session,
    construct_rpc_key("rise", "ssrs18", "set_manual_control_mapping", "mav/0"),
    mapping.SerializeToString(),
    response_cls=ManualControlMappingAck)

# 2) Publish stick values at ~10 Hz. The connector composes the
#    RC_CHANNELS_OVERRIDE frame on every arrival.
x_pub = session.declare_publisher(
    construct_pubsub_key("rise", "ssrs18", "joystick_x_pct", "joystick-1"))
y_pub = session.declare_publisher(
    construct_pubsub_key("rise", "ssrs18", "joystick_y_pct", "joystick-1"))

while driving:
    x_pub.put(enclose(TimestampedFloat(value=steering_pct).SerializeToString()))
    y_pub.put(enclose(TimestampedFloat(value=throttle_pct).SerializeToString()))
    time.sleep(0.1)

# 3) To stop: simply stop publishing. ArduPilot's RC override expires
#    after ~3 s of silence. Alternatively, call set_manual_control_mapping
#    again with an empty axes map.
```
