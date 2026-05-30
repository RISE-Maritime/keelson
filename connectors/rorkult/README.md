# keelson-connector-rorkult

Bidirectional bridge between the Keelson bus and a companion microcontroller ("rorkult", placeholder name) over TCP.

**Status:** skeleton only. The TCP transport, asyncio loop, Zenoh wiring, and RPC scaffolding are in place; the MCU wire format (framing + command/response protocol) is deliberately deferred to a follow-up PR. RPC handlers respond with `COMMAND_RESULT_UNSUPPORTED` until framing lands.

## What this connector is for

The "true" connector in the rover stack: everything else under the rover heading (estimator, guidance, safety supervisor) is a *processor* on the bus, while this one bridges Keelson to external hardware via TCP. Modeled on `connectors/mavlink` — bidirectional, single process, single MCU connection.

## Interfaces (when framing lands)

- **`VehicleControl`** — `set_control_mapping` / `get_control_mapping`. Operator wires `"steering"` / `"throttle"` to existing `TimestampedFloat` subjects (joystick from a gamepad, guidance setpoints from a downstream processor, or any mix).
- **`VehicleLifecycle`** — `arm` / `set_mode` / `emergency_stop`.
- `VehicleParam` and any `ActuationCommand` escape hatch are deliberately out of v1 scope.

## Layout

```
connectors/rorkult/
├── bin/keelson2rorkult.py       Entry point
├── rorkult/
│   ├── transport.py             Transport ABC + TcpTransport + reconnect backoff
│   └── framing.py               Framing ABC + PassthroughFraming stub
├── tests/
└── pyproject.toml
```
