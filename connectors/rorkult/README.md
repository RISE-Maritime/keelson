# keelson-connector-rorkult

Bidirectional bridge between the Keelson bus and a companion microcontroller ("rorkult" — placeholder name until the device's real name lands) over TCP.

**Status:** skeleton. The TCP transport, asyncio loop, Zenoh wiring, RPC scaffolding, and `entity_health` publishing are in place; the MCU wire format (framing + command/response protocol) is deliberately deferred to a follow-up PR. RPC handlers respond with `COMMAND_RESULT_UNSUPPORTED` (or `reply_err`) until framing lands.

## What this connector is for

Bridging the Keelson bus to an external actuation MCU. The MCU receives commanded actuator setpoints (steering, throttle, …) over TCP and reports back measured actuator state plus a heartbeat. Modeled on `connectors/mavlink` — bidirectional, single process, single MCU connection per process instance.

## Interfaces (when framing lands)

- **`VehicleControl`** — `set_control_mapping` / `get_control_mapping`. Operator wires `"steering"` / `"throttle"` to existing `TimestampedFloat` subjects; the publisher can be anything (joystick driver, a guidance processor, an MCAP replay).
- **`VehicleLifecycle`** — `arm` / `set_mode` / `emergency_stop`.
- `VehicleParam` and any `ActuationCommand` escape hatch are deliberately out of v1 scope.

## Published today (skeleton)

- `entity_health` (`keelson.EntityHealth`) — `HEALTH_NOMINAL` when the TCP connection to the MCU is up, `HEALTH_CRITICAL` otherwise. Published periodically (default 1 Hz) and on every state change.
- Liveliness token — signals **connector-alive**, distinct from MCU-alive (see `entity_health`).

## Layout

```
connectors/rorkult/
├── bin/keelson2rorkult.py       Entry point
├── rorkult/
│   ├── transport.py             Transport ABC + TcpTransport + reconnect backoff
│   ├── framing.py               Framing ABC + PassthroughFraming stub
│   └── health.py                HealthState + EntityHealth builder
├── tests/
└── pyproject.toml
```
