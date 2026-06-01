# keelson2rorkult — Zenoh API

**Status:** skeleton. Surface listed here is the target; until framing lands, RPC handlers return `COMMAND_RESULT_UNSUPPORTED` (or `reply_err` where the response has no result field), and the only telemetry the connector actually emits today is `entity_health`.

## Liveliness

One token declared at session open, undeclared on clean shutdown. Signals **connector-alive**, *not* MCU-alive — for MCU liveness, subscribe to `entity_health` (below).

## RPCs

| Service | Procedure | Status |
| --- | --- | --- |
| `VehicleControl` | `set_control_mapping` | stub (`reply_err`) |
| `VehicleControl` | `get_control_mapping` | stub (`reply_err`) |
| `VehicleLifecycle` | `arm` | stub (`UNSUPPORTED`) |
| `VehicleLifecycle` | `set_mode` | stub (`UNSUPPORTED`) — likely permanent `UNSUPPORTED` if rorkult has no modes |
| `VehicleLifecycle` | `emergency_stop` | stub (`UNSUPPORTED`) |

## Published telemetry

### Today (skeleton)

- `entity_health` (`keelson.EntityHealth`) under `--source-id`. Published at `--health-publish-rate-hz` (default 1 Hz). `level=HEALTH_NOMINAL` when the TCP connection to the MCU is up, `HEALTH_CRITICAL` otherwise (never-connected and disconnected both surface as `CRITICAL`). The detail string preserves the last transition's reason (`"connected to <host:port>"` or `"connect to <ep> failed: <type>: <msg>"` etc.).

### Target (when framing lands)

Two values per actuator — the setpoint rorkult forwarded to the MCU, and the value the MCU reports as measured. Same Keelson subject for both; distinguished by a sub-namespace on `source_id`:

| Subject | `source_id` | Meaning |
| --- | --- | --- |
| `rudder_angle_deg` | `<source-id>/setpoint` | The rudder setpoint actually forwarded to the MCU (after dead-man / rate-limiting / scaling — *not* the input that came in on the bus). |
| `rudder_angle_deg` | `<source-id>/measured` | The MCU's reported actual rudder position. |
| `engine_throttle_pct` | `<source-id>/setpoint` | Throttle setpoint forwarded to the MCU. |
| `engine_throttle_pct` | `<source-id>/measured` | MCU's reported actual throttle. |
| `vehicle_armed` | `<source-id>` | Armed-state echo from the MCU (single source, no sub-namespace). |

> The connector's `--source-id` is the un-suffixed value (e.g. `rorkult/0`); the `/setpoint` and `/measured` suffixes are appended only when constructing the publish key for those specific subjects. `entity_health` and the liveliness token use the plain `--source-id`. Wildcard subscribers (`{realm}/@v0/{entity}/pubsub/*/{source-id}/*`) pick up both sub-namespaces.

> The existing subject `engine_throttle_pct` is documented in `subjects.yaml` as "commanded throttle"; the comment is misleading for this connector (which uses the subject for both commanded and measured, distinguished by source_id). Treat the subject as the generic "engine throttle as a percentage" — `source_id` decides the role.

## Loopback guard

When `set_control_mapping` is implemented, the connector will reject any `ControlAxis.subject` whose resolved key would match its own `{entity_id, source_id}` (under any of the sub-namespaces) — the standard guard from `connectors/CLAUDE.md`. Currently inapplicable because the handler is stubbed.
