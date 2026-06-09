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
> first command) and is the right entry point. This file is the operator
> reference for CLI flags, install, and migration.
>
> **Implementing a client** (UI, autonomy stack, MCAP recorder, test
> fixture)? See [`ZENOH_API.md`](./ZENOH_API.md) — the complete contract
> between this connector and the Zenoh bus: every published subject,
> every RPC, the manual-control and sensor-injection downlink shape, and
> end-to-end client snippets.

> **Breaking changes (in-progress branch `feature/mavlink-connector`).**
> Every typed-payload pub/sub command has been promoted to an RPC, and
> all connector-specific payload types have been removed. The connector
> now consumes only **existing** Keelson subjects on the bus —
> telemetry on the uplink, joystick / wheel / lever subjects for
> stick-driving, location_fix / location_fix_quality / … for injection.
>
> | Removed subject / type | Use instead |
> | --- | --- |
> | `cmd_goto` (pub/sub) | `set_navigation_target` RPC (`interfaces/VehicleNavigation.proto`) |
> | `cmd_set_cruise_speed` (pub/sub) | `set_cruise_speed` RPC (`VehicleNavigation`) |
> | `cmd_arm` (pub/sub) | `arm` RPC (`interfaces/VehicleLifecycle.proto`) |
> | `cmd_set_mode` (pub/sub) | `set_mode` RPC (`VehicleLifecycle`) |
> | `cmd_emergency_stop` (pub/sub) | `emergency_stop` RPC (`VehicleLifecycle`) |
> | `cmd_save_params` (pub/sub) | `save_params` RPC (`VehicleParam`) |
> | `cmd_reboot` (pub/sub) | `send_command_long` RPC (`MavlinkCommand`) with `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` (246) — see "Rebooting the autopilot" below |
> | `cmd_clear_mission` (pub/sub) | `clear_mission` RPC (`interfaces/VehicleMission.proto`) |
> | `cmd_set_current_waypoint` (pub/sub) | `set_current_waypoint` RPC (`VehicleMission`) |
> | `cmd_enable_geofence` (pub/sub) | `enable_geofence` RPC (`interfaces/VehicleGeofence.proto`) |
> | `manual_control` subject + `keelson.ManualControl` payload | Existing `joystick_x_pct` / `joystick_y_pct` / `wheel_position_pct` / etc., wired to MAVLink RC channels via the `VehicleControl.set_control_mapping` RPC (`interfaces/VehicleControl.proto`) |
> | `cmd_active_source` / `active_command_source` (pub/sub) | Removed — never had a producer or consumer; declared aspirationally |
> | `inject_*` (8 subjects) | `--injection-config <yaml>` (see "Downlink: sensor injection") |
>
> Also renamed: `MavlinkParam` → `VehicleParam`, `MavlinkMission` →
> `VehicleMission`, `MavlinkGeofence` → `VehicleGeofence`. New interface
> file: `VehicleControl.proto` (per-axis control mapping).
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
| `--source-system` | `254` | MAVLink `system_id` we send out as. Defaults to `254` so we don't collide with `blueos-gateway` (which uses `255`) during parallel-deploy migration. Must match the autopilot's `SYSID_MYGCS` or ArduPilot silently drops `manual_control`'s `RC_CHANNELS_OVERRIDE`; the connector reads `SYSID_MYGCS` at startup and logs a `WARNING` on a mismatch. |
| `--source-component` | `MAV_COMP_ID_ONBOARD_COMPUTER` (191) | MAVLink `component_id` we send out as. |
| `--target-component` | `0` (any) | Filter incoming messages by source component. |
| `--recv-timeout` | `1.0` | Per-recv timeout in seconds. Controls how quickly the connector reacts to SIGINT. |
| `--link-timeout` | `10.0` | Seconds of total MAVLink silence before the connector concludes the link is dead, logs an error, and exits non-zero so a process supervisor can restart it. A dropped **TCP** link is detected immediately via transport EOF, regardless of this value. `0` disables the silence watchdog (TCP EOF detection still applies). For a `tlog:` replay URL, a drained file trips this as a clean end-of-replay (exit `0`). |
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

## Zenoh interface — at a glance

> **Full reference:** [`ZENOH_API.md`](./ZENOH_API.md) — every published
> subject, every RPC, the manual-control and sensor-injection downlink
> shape, response-type semantics, recommended client timeouts, and
> end-to-end client snippets.

Three Keelson key formats are used; the connector substitutes `--realm`,
`--entity-id`, and `--source-id` into each:

```
Pub/sub      : {realm}/@v0/{entity_id}/pubsub/{subject}/{source_id}
RPC          : {realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}
Liveliness   : {realm}/@v0/{entity_id}/pubsub/*/{source_id}
```

Three categories of traffic across these keys:

| Direction | Pattern | Examples | Where to look |
| --- | --- | --- | --- |
| Uplink | Pub/sub publish (Keelson Envelopes wrapping typed payloads) | `vehicle_mode`, `location_fix`, `roll_deg`, `entity_health`, … (29 subjects, 14 source MAVLink message types) | [ZENOH_API.md § Published telemetry](./ZENOH_API.md#published-telemetry-uplink) |
| Downlink data | Pub/sub *subscribe* on existing controller-input + telemetry subjects | `joystick_x_pct` → MAVLink `RC_CHANNELS_OVERRIDE`; `location_fix` (with a different source_id) → MAVLink `GPS_INPUT` | [§ Manual control](./ZENOH_API.md#manual-control), [§ Sensor injection](./ZENOH_API.md#sensor-injection) |
| Commands | Queryable RPCs (20 procedures across 6 `Vehicle*` services + 1 escape hatch) | `arm`, `set_mode`, `upload_mission`, `set_navigation_target`, `get_param`, … | [§ RPC services](./ZENOH_API.md#rpc-services) |

The connector subscribes to **no** downlink subjects by default — the
operator wires them up explicitly via the
`VehicleControl.set_control_mapping` RPC (control-axis driving) and the
`--injection-config` YAML (sensor injection). Same architectural shape for
both: existing Keelson subjects on the data plane, operator-declared
mapping on the control plane.

The liveliness token signals **connector-alive**, not vehicle-alive. Use
the freshness of `entity_health` (republished from every MAVLink HEARTBEAT)
for vehicle liveness.

---

## Security

**The connector's entire command surface is unauthenticated.** Every RPC —
`arm`, `set_mode`, `emergency_stop`, mission and geofence uploads, parameter
writes, the `send_command_long` escape hatch — is served as a plain Zenoh
queryable with **no authentication and no authorization**. Any client that
can reach the connector's `@rpc` keyspace can arm and drive the vessel.

There is no application-level access control, and none is planned at the
connector level — restricting who can reach these keys is a **deployment
responsibility**:

- **Do not bridge the connector's `@rpc` keyspace onto an untrusted or
  shared Zenoh router.** If the local router is bridged to a wider fleet
  or cloud bus, anyone on that bus can command the vehicle with a single
  query. Treat Zenoh reachability of
  `{realm}/@v0/{entity_id}/@rpc/**` as equivalent to physical access to
  the helm.
- Keep the connector's Zenoh peer on a trusted network segment, or apply
  access control — TLS with mutual authentication, and an ACL restricting
  the `@rpc` keyspace — on the routers that carry its traffic. Zenoh's own
  access-control configuration is the right layer for this; the connector
  does not attempt to duplicate it.
- Telemetry (the pub/sub uplink) is read-only and lower-risk, but the
  same reachability rules apply if your deployment treats vehicle
  position as sensitive.

This is a known limitation, stated here so that exposing the command
surface is a deliberate deployment decision rather than a surprise.

---

## Rebooting the autopilot

There is **no typed `reboot` RPC**. Rebooting or shutting down the
autopilot does not abstract cleanly across autopilot stacks and host
platforms, so it is left to the `send_command_long` escape hatch
(`MavlinkCommand`):

```python
from keelson.interfaces.MavlinkCommand_pb2 import CommandLongRequest

# MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN = 246
#   param1 = 1  reboot autopilot
#          = 2  shutdown autopilot
#          = 3  reboot autopilot, keep it in the bootloader
req = CommandLongRequest(command=246, param1=1)
# ... send to {realm}/@v0/{entity_id}/@rpc/send_command_long/{source_id}
```

The autopilot almost always drops the MAVLink link as it reboots, before
its `COMMAND_ACK` reaches the connector, so a `TIMEOUT` result is the
expected common case and not itself a failure — reconnection telemetry
is the real success signal.

**BlueOS / Navigator caveat.** On BlueOS-based platforms ArduPilot runs
as a managed process; `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` exits it and
the platform does **not** auto-relaunch it — even `param1=1` ("reboot")
behaves as a shutdown. Recovery requires BlueOS's ardupilot-manager
`/restart` API, which this connector deliberately does not talk to.
Don't issue a reboot on a BlueOS vehicle unless you have another way to
bring ArduPilot back.

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

E2e coverage spans the telemetry path (tlog replay + live SITL), the
manual-control RPC + per-axis pub/sub data plane, file-driven GPS
injection, every Vehicle* RPC service, the recv-race regression
(50-call arm/disarm stress + telemetry-flow assertion), and proto
round-trips for the typed `Mission` and `Geofence` shapes. The
canonical list is what `pytest --collect-only -m e2e` prints; the
intent of each test is documented in its docstring.

The SITL fixture (`_sitl_rover` in `tests/test_mavlink_e2e.py`) waits for
a HEARTBEAT before yielding the port, and `_wait_for_connector_ready`
subscribes to `vehicle_mode` and waits for the first envelope before the
test acts — so failures land with actionable error messages instead of
fixed-sleep flakiness.
