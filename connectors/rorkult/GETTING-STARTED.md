# Getting started: bridging a companion MCU to Keelson

This guide walks through running `keelson-connector-rorkult` against a companion microcontroller ("MCU") that speaks TCP. The connector:

- Maintains the MCU's TCP connection (auto-reconnect with exponential backoff).
- Reports MCU-link health on the Keelson bus as `entity_health`.
- Exposes the `VehicleControl` RPC surface so an operator can wire control axes to existing Keelson subjects (joystick inputs, guidance setpoints, …) — those subscribers are installed and run, but the actual MCU forward step is a no-op until **MCU framing is decided** (see the [Status](#status) section below).

You don't need MCU experience. Concepts are introduced as they come up.

---

## Who this is for

- You have an actuation MCU (or are planning one) that listens on a TCP port.
- You want telemetry from / commands to that MCU to appear on a Keelson realm so other tools (MCAP recording, Foxglove dashboards, autonomy code) can interact with it through the standard Keelson primitives.
- You're OK running a skeleton today and adding the wire-format piece later — the parts that *are* implemented (link health, axis-mapping plumbing) are useful on their own.

If you have an autopilot that already speaks MAVLink, you almost certainly want `connectors/mavlink` instead.

---

## Status

The connector is a **skeleton**. What works today:

| Capability | Status |
| --- | --- |
| Connect to a TCP MCU with auto-reconnect | ✅ |
| Publish `entity_health` reflecting link state + transport metrics | ✅ |
| `VehicleControl.set_control_mapping` / `get_control_mapping` RPCs | ✅ (subscribers installed, scaling/dead-man/loopback all real) |
| Encode + forward commands to the MCU | ❌ (framing-gated; the forward step debug-logs the unit values it *would* send) |
| `VehicleLifecycle.arm` / `set_mode` / `emergency_stop` | ❌ (returns `COMMAND_RESULT_UNSUPPORTED`; needs framing for MCU semantics) |
| Publish MCU-measured actuator state | ❌ (framing-gated) |
| Publish forwarded setpoints under `{source_id}/setpoint` | ❌ (framing-gated; needs MCU calibration too) |

You can deploy the skeleton today and get useful operational signal from `entity_health` while the wire format is being designed.

---

## The big picture

```
            Equipment side                                 Operator side
+-------------------------+                  +--------------------------------+
| Companion MCU           |                  |                                |
|  - Listens on TCP port  |                  |  Your Keelson tools:           |
|  - Drives ESC + servos  |                  |   - Foxglove dashboard         |
|    (or whatever)        |                  |   - MCAP recorder              |
+----------+--------------+                  |   - Autonomy / joystick code   |
           | TCP                             |                                |
           |                                 +-------------+------------------+
+----------+--------------+                                |
| Companion computer      |                                |
|  - Pi / NUC / SBC       |  Zenoh peer-to-peer            |
|  - Runs:                |================================+
|    keelson2rorkult      |  (Wi-Fi / cellular / radio)
+-------------------------+
```

The companion computer is the bridge; everything downstream talks pure Keelson.

---

## What you need

### On the equipment side

- A device that accepts a TCP connection on a known host:port. For early bring-up, that can be a stand-in (`socat`, a small Python `asyncio.start_server` script — see the [Smoke-test](#smoke-test-without-a-real-mcu) section).
- Network reachability from the companion computer to the MCU (same LAN or routed network).

### On the companion computer

- Python 3.11+
- The Keelson SDK and this connector. From the keelson repo:

  ```sh
  uv sync --all-packages
  ```

- A working Zenoh setup — either pointing at a Zenoh router elsewhere in your fleet, or running as a peer. The Keelson docs cover both setups.

---

## Step 1 — pick your identifiers

The connector publishes under a `{realm}/@v0/{entity-id}/.../{source-id}` key structure. Decide once:

- `--realm` — top-level org / project namespace (e.g. `rise`).
- `--entity-id` — the platform this MCU lives on (e.g. `boat-01`).
- `--source-id` — a per-MCU identifier under the entity (e.g. `rorkult/0`). If you ever run a second MCU on the same platform, pick a different source-id (`rorkult/1`) — one process per MCU.

These are the same identifiers any other Keelson connector on this platform uses; consistency makes downstream tooling (Foxglove, MCAP) simpler.

---

## Step 2 — launch the connector

```sh
keelson2rorkult \
  --realm rise \
  --entity-id boat-01 \
  --source-id rorkult/0 \
  --mcu-endpoint 192.0.2.50:9000 \
  --connect tcp/192.0.2.10:7447     # your Zenoh router; --mode peer for peer-to-peer
```

What you should see in stderr within a couple of seconds:

```
INFO Opening Zenoh session...
INFO Declared liveliness token (connector alive)
INFO Publishing entity_health on rise/@v0/boat-01/pubsub/entity_health/rorkult/0
INFO Declared RPC queryable: rise/@v0/boat-01/@rpc/set_control_mapping/rorkult/0
INFO Declared RPC queryable: rise/@v0/boat-01/@rpc/get_control_mapping/rorkult/0
INFO Declared RPC queryable: rise/@v0/boat-01/@rpc/arm/rorkult/0
INFO Declared RPC queryable: rise/@v0/boat-01/@rpc/set_mode/rorkult/0
INFO Declared RPC queryable: rise/@v0/boat-01/@rpc/emergency_stop/rorkult/0
INFO Connecting to MCU at 192.0.2.50:9000
INFO MCU connected at 192.0.2.50:9000    # (if the MCU is up)
```

If the MCU is unreachable, you'll see backoff retries instead — that's expected, and the connector stays up:

```
WARNING MCU connect to 192.0.2.50:9000 failed (TimeoutError): ...
INFO Connecting to MCU at 192.0.2.50:9000
WARNING MCU connect to 192.0.2.50:9000 failed (ConnectionRefusedError): ...
...
```

---

## Step 3 — verify on the bus

From any machine on the Zenoh network:

```sh
zenoh subscribe 'rise/@v0/boat-01/pubsub/entity_health/rorkult/0'
```

You should see one Keelson-enveloped `EntityHealth` message per second (or whatever `--health-publish-rate-hz` is set to — default 1.0). The payload contains one `SourceHealth` named `mcu_link` carrying a `tcp_connection` subject with four checks:

| Check | What it means |
| --- | --- |
| `connected` | `HEALTH_NOMINAL` if TCP up, `HEALTH_CRITICAL` otherwise. `detail` carries the last transition reason. |
| `connect_attempts_since_success` | Count since the last successful connect. `0` while stable. |
| `bytes_received_total` | Monotonic over the process lifetime. |
| `last_byte_received` | Human-readable `"Xs ago"` or `"never"`. |

The top-level `EntityHealth.level` mirrors `connected` (the only real verdict today).

---

## Step 4 — wire a control mapping (skeleton-mode)

`VehicleControl.set_control_mapping` accepts a mapping of axis-name → Keelson subject. v1 recognises `"steering"` and `"throttle"`. The connector installs a subscriber per axis, applies scaling / polarity / inversion, runs the dead-man timer, *and would forward to the MCU* — that last step is a debug-log line for now.

A minimum operating call:

```python
import zenoh
from keelson import construct_rpc_key
from keelson.interfaces.VehicleControl_pb2 import (
    ControlAxis, ControlAxisMapping, ControlAxisMappingAck,
)

mapping = ControlAxisMapping(
    axes={
        "steering": ControlAxis(subject="joystick_x_pct", source_id="gamepad-1"),
        "throttle": ControlAxis(subject="joystick_y_pct", source_id="gamepad-1"),
    },
    min_interval_s=0.05,   # cap forward rate at 20 Hz
    max_axis_age_s=0.5,    # dead-man: stop forwarding if either axis goes silent for >0.5 s
)

with zenoh.open(zenoh.Config()) as session:
    key = construct_rpc_key("rise", "boat-01", "set_control_mapping", "rorkult/0")
    replies = []
    session.get(key, lambda r: replies.append(r), payload=mapping.SerializeToString())
    # ... wait for reply, parse ControlAxisMappingAck() ...
```

Connector logs after a successful mapping + a publisher actually publishing `joystick_x_pct` + `joystick_y_pct` at the connector's source_id:

```
INFO control axis steering: subscribing to rise/@v0/boat-01/pubsub/joystick_x_pct/gamepad-1 (unipolar=False invert=False)
INFO control axis throttle: subscribing to rise/@v0/boat-01/pubsub/joystick_y_pct/gamepad-1 (unipolar=False invert=False)
INFO control engaged: forwarding to MCU (axes=['steering', 'throttle'], dead-man=0.50s)
DEBUG would forward to MCU (framing stubbed): {'steering': '+0.500', 'throttle': '-0.200'}
```

`control engaged` fires once when both axes have published at least one sample. `control disengaged` fires once when any axis goes stale past `max_axis_age_s`.

---

## Smoke-test without a real MCU

You don't need MCU hardware to verify the connector reaches the bus and reports health correctly. The simplest stand-in is `socat` in listen-and-discard mode:

```sh
socat TCP-LISTEN:9000,reuseaddr,fork - >/dev/null
```

Then launch the connector against `127.0.0.1:9000`. You should see `MCU connected at 127.0.0.1:9000`, `entity_health` flips to `HEALTH_NOMINAL`, and `connect_attempts_since_success` resets to `0`.

To exercise the disconnect path: kill `socat` (`Ctrl-C`). The supervisor's read raises `ConnectionError`, `entity_health` flips back to `HEALTH_CRITICAL`, and the reconnect backoff loop kicks in.

---

## Troubleshooting

**Connector exits immediately at startup.**
Almost always a CLI parse error or a Zenoh config issue. Re-run with `--log-level 10` (DEBUG) for verbose output. The `--mode {peer,client}` + `--connect` / `--listen` flags follow the standard Keelson scaffolding (`add_common_arguments`).

**Connector runs but `entity_health` never flips to NOMINAL.**
The MCU isn't reachable. Check `connect_attempts_since_success` on `entity_health` — it should climb. Check `detail` on the `connected` check for the OS-level error (`ConnectionRefusedError`, `TimeoutError`, etc.). From the same companion computer, try `nc -v <mcu-host> <mcu-port>` — same result.

**Connector reports `HEALTH_CRITICAL` with `link to <ep> dropped` on clean shutdown.**
Cosmetic. The supervisor marks the link as disconnected when the `is_shutdown` flag flips so any consumer still polling sees a non-connected state, even though it's a clean exit.

**`set_control_mapping` returns an `ErrorResponse` with `"loopback"`.**
You're trying to subscribe to a subject under the connector's own `{entity_id, source_id}` (or a wildcard that overlaps it). The connector publishes `entity_health` and (future) actuator subjects under that source_id; subscribing to a key the connector publishes would loop. Pick a more specific source_id pattern.

**`set_control_mapping` returns `"unknown axis 'X'"`.**
v1 axis vocabulary is `{"steering", "throttle"}`. Future axes (`roll`, `pitch`, `yaw`, `brake`, `gear`) will be added to `RECOGNISED_AXES` in `rorkult/control_axis.py` as the MCU gains them.

**`arm` / `set_mode` / `emergency_stop` all return `COMMAND_RESULT_UNSUPPORTED`.**
Expected: these need MCU framing to know how to express the operation on the wire. They'll come online with framing.

---

## What lands next

The MCU wire-format decision (`Framing` implementation + the `_emit` body in `ControlAxisState`). When that lands:

- The "would forward" debug logs become real MCU writes.
- The forwarded unit values get published under `{source_id}/setpoint` (with operator-configured actuator-units conversion).
- MCU-reported actuator state gets published under `{source_id}/measured`.
- `VehicleLifecycle.arm` / `set_mode` / `emergency_stop` get real handlers.
- `entity_health.last_byte_received` gains a freshness gate based on the expected MCU heartbeat cadence.
