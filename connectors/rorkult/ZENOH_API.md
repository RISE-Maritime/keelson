# keelson2rorkult — Zenoh API

**Status:** skeleton. Surface listed here is the *target*; until framing lands, RPC handlers return `COMMAND_RESULT_UNSUPPORTED`.

## Liveliness

One token declared at session open, undeclared on clean shutdown. Signals **connector-alive**, *not* MCU-alive — MCU liveness will surface as `entity_health` once framing lands.

## RPCs (target shape)

| Service | Procedure | Status |
| --- | --- | --- |
| `VehicleControl` | `set_control_mapping` | stub (UNSUPPORTED) |
| `VehicleControl` | `get_control_mapping` | stub (UNSUPPORTED) |
| `VehicleLifecycle` | `arm` | stub (UNSUPPORTED) |
| `VehicleLifecycle` | `set_mode` | stub (UNSUPPORTED) — likely permanent UNSUPPORTED if rorkult has no modes |
| `VehicleLifecycle` | `emergency_stop` | stub (UNSUPPORTED) |

## Published telemetry (target, when framing lands)

All under the connector's own `source_id`:

| Subject | Payload |
| --- | --- |
| `rudder_angle_deg` | `keelson.TimestampedFloat` |
| `autopilot_throttle_pct` | `keelson.TimestampedFloat` |
| `vehicle_armed` | `keelson.TimestampedBool` |
| `entity_health` | `keelson.EntityHealth` |
