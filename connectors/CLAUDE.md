# Connectors

Zenoh connectors that ingest/export data between external systems and the Keelson bus.

## Interface design principles

These principles came out of the bigger MAVLink connector overhaul. They apply
to every connector that integrates an external protocol with Keelson. If
you're about to add a new subject, payload type, or RPC, work through this
list first.

### Keep external protocol shapes out of Keelson interfaces

The interfaces under `interfaces/` are supposed to be vehicle-agnostic — any
other connector implementing the same protocol (e.g. a ROS bridge, a
proprietary autopilot integration) should be able to expose the same RPC
contract without callers caring which one is on the other end. That promise
is easy to break and easy to spot in review.

- **Don't propagate degE7 / fixed-point scaling, magic command numbers, or
  opaque `(field1, field2, field3, field4)` blobs.** The original `MissionItem`
  was MAVLink `MISSION_ITEM_INT` in disguise: `sint32 x`/`y` in degE7, a
  `command` field overloaded by MAV_CMD numbering, and four `param1..param4`
  whose meaning shifted per command. The replacement is a `oneof step` over
  typed `Waypoint`/`Loiter`/`Delay`/`ReturnHome`/… messages with a shared
  `Coordinate { double latitude_deg; double longitude_deg }` primitive. Same
  expressiveness, no leak.
- **Use `oneof` for variant types.** When a message can be one of N things
  (mission step types, fence shapes), encode that explicitly via `oneof`
  rather than via a discriminator enum + opaque payload. Readers see all the
  legal cases at a glance; predicates become typed; missing cases become
  build errors when you add a new variant.
- **Closed sets over opaque escape hatches.** A closed `oneof` is better than
  a "param blob" with unwritten conventions, even if it means you can't pass
  through MAV_CMDs you don't model. The right move on download is to **raise**
  on unknown command types rather than silently mapping to a default-shaped
  item. If you really need a raw pass-through, expose it as a separate
  intentionally-protocol-shaped RPC (e.g. `MavlinkCommand.send_command_long`)
  with the leak documented at the proto-comment level.
- **One enum for outcomes, one int field for the raw code.** All RPC
  responses share `CommandResult result + int32 raw_autopilot_result +
  string detail` (see `interfaces/VehicleCommon.proto`). The typed enum gives
  callers a stable vocabulary; the raw int preserves codes the enum doesn't
  yet model. Don't return strings for outcomes ("accepted" / "denied" / ...);
  they leak the protocol's vocabulary into every consumer.
- **Document deliberate leaks.** `MavlinkCommand` is intentionally
  MAVLink-shaped (it's the escape hatch). Flag this in the proto comment so
  future readers don't mistake it for an oversight.

### Pub/sub vs RPC: pick by data shape, not by convenience

| Use pub/sub when… | Use a queryable RPC when… |
| --- | --- |
| The data is continuous (telemetry, sensor streams, controller inputs) | The interaction is one-shot (command, query, configuration change) |
| Multiple consumers want the same stream | One caller expects one response |
| "Stop publishing" is the natural stop signal | The caller wants typed success/failure semantics |
| Late joiners don't need history | Late "joining" doesn't apply — every call is its own session |

The historical `cmd_*` pub/sub subjects (`cmd_arm`, `cmd_set_mode`,
`cmd_goto`, …) were a bad fit: every one had an implicit "but did it work?"
that pub/sub doesn't model. Every one became an RPC in this refactor. If you
find yourself adding a `cmd_*` subject, you almost certainly want an RPC.

### Reuse existing subjects; don't invent connector-specific ones

When a connector consumes data from the bus (manual control inputs, sensor
injection), the **data plane** should be plain Keelson pub/sub on **existing**
subjects with **existing** payload types. The connector's interface is the
*configuration of which Zenoh keys to subscribe to*, not the stream itself.

- Manual control reads from `joystick_x_pct`, `joystick_y_pct`,
  `wheel_position_pct`, … all `keelson.TimestampedFloat`. No connector
  invents a `ManualControl` payload type — that was the old design and got
  removed.
- Sensor injection reads from `location_fix`, `location_fix_quality`, … all
  existing Keelson types. Same subject can carry "vehicle's reported GPS" on the
  uplink and "external GPS to fuse" on the downlink, distinguished only by
  `source_id`.

The control plane (which Zenoh keys to subscribe to) is the connector's
own interface — it's where typed config belongs. Choose RPC for the mapping
when live reconfiguration matters; choose YAML / CLI when the mapping is
deployment-static and benefits from version-control.

### One protocol instance = one entity = one connector process

Don't multiplex multiple vehicles / devices / streams through one connector.
The `--target-system` filter exists to enforce this in MAVLink's case — drop
anything not from the expected sender — and `--entity-id` pins that to a
single Keelson entity. If you have two vehicles, run two connectors with
distinct `--entity-id` values.

This keeps connector code single-tenant (no per-vehicle state demuxing in
hot paths), keeps debugging tractable (one process, one log stream, one
liveliness token), and makes the deployment story uniform across connector
types.

### Loopback guards

A connector that publishes to subject `X` on its own entity and *also*
subscribes to subject `X` (perhaps under a different `source_id`) is a
foot-gun for the operator. Reject, at config-validation time, any
subscription pattern that would match the connector's own `--source-id` on
its own `--entity-id`.

The MAVLink connector does this for both the manual-control mapping RPC and
the injection-config YAML — failing fast at startup is much better than
discovering the autopilot's published GPS is being injected back into the
autopilot's EKF as an "external" measurement.

### Cross-entity inputs need explicit identification

Subscriptions default to the connector's own `--entity-id`, but should accept
an explicit `entity_id` to support cross-entity data flows (the canonical
case: RTCM corrections from a shore-side RTK base flowing into multiple
vehicle connectors). Don't bake the connector's own entity into the
subscription key construction — make it a default that an explicit config
value can override.

### Liveliness signals connector-alive, not external-system-alive

Declare exactly one liveliness token per connector at session-open time and
undeclare it on clean shutdown. Treat it as "this process is connected to
Zenoh." Don't gate on external-system readiness — that's a different signal
(use `entity_health` republished from the protocol's heartbeat / status
stream for that). An aggregator rolling up health across multiple sources
should consume the typed health subject, not the liveliness token.

### Stream semantics: silence is a signal too

When the external system has a natural "expiry on silence" behaviour (e.g.
ArduPilot's RC override expires after ~3 s of no input), use that — don't
invent an explicit "stop" command. **"Stop publishing" *is* the stop
signal.** Callers find this surprising but it's the honest contract; the
alternative is a parallel state-machine that drifts from the protocol's
actual behaviour.

### Long-running RPC handlers need their own thread

A multi-second RPC handler (`list_params`, mission upload) must not block
telemetry. The MAVLink connector uses the per-Queryable callback thread
that `zenoh-python` spawns by default (one thread per queryable with
`indirect=True`) — different procedures run concurrently; same procedure
serialises. If you're writing a connector with a similar shape, don't
collapse all RPCs onto a single drain loop; let Zenoh's threading give you
per-procedure concurrency for free.

### Document the proto comments at the source-of-truth level

When a `Vehicle*` proto field is unavoidably shaped by an underlying
protocol (e.g. `mode` is a free-form string because vehicle modes don't
cleanly abstract across autopilot stacks), say so in the proto comment.
Don't leave it to future readers to figure out from the connector source.
The proto is the contract; the comment is part of it.

## Standard Layout

```
connectors/{name}/
├── bin/              Entry-point scripts (*.py)
├── tests/
│   ├── conftest.py   Connector-specific fixtures
│   ├── test_*_cli.py
│   └── test_*_e2e.py
├── pyproject.toml    Package config (workspace member)
└── requirements.txt  Non-Python/extra dependencies
```

## Standard Connector Pattern

```python
import argparse
from keelson.scaffolding import (
    add_common_arguments, create_zenoh_config,
    setup_logging, GracefulShutdown, declare_liveliness_token,
)

parser = argparse.ArgumentParser()
add_common_arguments(parser)
# ... connector-specific args ...
args = parser.parse_args()

setup_logging(args)
conf = create_zenoh_config(args)

session = zenoh.open(conf)
shutdown = GracefulShutdown()
token = declare_liveliness_token(session, args)

while not shutdown.is_shutdown():
    # ... main loop ...
```

## Global PUBLISHERS Dict

Most connectors cache publishers in a module-level dict to avoid re-declaring:

```python
PUBLISHERS: dict[str, zenoh.Publisher] = {}
```

This must be cleared between tests (see Testing section below).

## Testing

### Commands

```bash
uv run pytest -vv -m "not e2e" connectors/          # All unit tests
uv run pytest -vv -m e2e connectors/                 # All e2e tests
uv run pytest -vv connectors/mcap/tests/             # Single connector
uv run pytest -vv connectors/nmea/tests/ -k "test_name"  # Single test
```

### Root conftest.py Fixtures

The root `conftest.py` provides shared fixtures for all connector tests:

- `ConnectorProcess` — manages connector subprocess lifecycle (start/stop/logs)
- `run_connector` — run a connector synchronously with timeout
- `connector_process_factory` — create managed ConnectorProcess instances
- `zenoh_port` / `zenoh_endpoints` — free port and listen/connect endpoint pairs
- `temp_dir` / `temp_file` — temporary file management
- `get_python_interpreter()` — finds Python with zenoh available
- `BINARY_NAME_MAP` — maps test names to actual script filenames

### Skarv State Cleanup

Connectors using `skarv` (nmea, ais) **must** clear skarv state between tests. Add this autouse fixture to the connector's `conftest.py`:

```python
@pytest.fixture(autouse=True)
def clear_skarv():
    """Clear skarv vault and caches to prevent cross-test pollution."""
    import skarv
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()
    yield
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()
```

### Module State Clearing

Clear the global `PUBLISHERS` dict and other module state between tests:

```python
@pytest.fixture(autouse=True)
def clear_module_state():
    my_connector.PUBLISHERS.clear()
    yield
    my_connector.PUBLISHERS.clear()
```

### Importing bin/ Scripts

Connector scripts in `bin/` are standalone executables, not packages. Use `SourceFileLoader`:

```python
import importlib.util
from importlib.machinery import SourceFileLoader

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"

_path = BIN_ROOT / "ais2keelson.py"
_loader = SourceFileLoader("ais2keelson", str(_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
ais2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ais2keelson)
```

### Mock Zenoh Objects

```python
from unittest.mock import Mock

@pytest.fixture
def mock_zenoh_session():
    session = Mock()
    publisher = Mock()
    publisher.published_data = []
    publisher.put = Mock(side_effect=lambda data: publisher.published_data.append(data))
    session.declare_publisher = Mock(return_value=publisher)
    return session
```

## Adding a New Connector

1. Create `connectors/{name}/` with `bin/`, `tests/`, `pyproject.toml`, `requirements.txt`
2. `pyproject.toml`: name `keelson-connector-{name}`, `requires-python = ">=3.11"`, workspace source for keelson
3. Add to root `pyproject.toml` workspace members and dependencies
4. Re-lock and re-export: `uv lock && uv export --frozen --format requirements-txt --no-emit-workspace --no-hashes --no-dev -o requirements-prod.txt` (the docker image installs from `requirements-prod.txt`, not from the per-connector requirements.txt; the lint job will fail if these are out of sync)
5. Add to `docker/Dockerfile`: the new connector's `bin/*.py` COPY line (deps come in via `requirements-prod.txt`, so no per-connector `pip install -r` line needed)
6. Add smoke test in `.github/workflows/ci.yml` docker-build job
7. Add testpath to root `pyproject.toml` `[tool.pytest.ini_options]`
8. Write tests with conftest.py following patterns above

## Connector-Specific Notes

| Connector | Notes |
|---|---|
| **mcap** | MCAP file recording/replay. `keelson2mcap.py` (712 lines) supports rotation. Also `mcap2keelson.py` (replay) and `mcap-tagg.py` (annotation). |
| **nmea** | 4 binaries: `nmea01832keelson`, `keelson2nmea0183`, `n2k2keelson`, `keelson2n2k`. `n2k2keelson`/`keelson2n2k` open a CAN gateway directly via the shared `bin/n2k_gateway.py`. Uses skarv for message routing. |
| **ais** | 3 binaries: `ais2keelson`, `keelson2ais`, `digitraffic2keelson`. Uses pyais, geopy, paho-mqtt. Skarv for routing. |
| **camera** | OpenCV-based video capture. Single binary `camera2keelson`. |
| **klog** | Binary KLOG format recording. `keelson2klog` + `klog2mcap` converter. |
| **foxglove** | Foxglove Studio live view bridge. Single binary `keelson2foxglove`. |
| **mediamtx** | WHEP/WebRTC proxy. Single binary `mediamtx-whep`. |
| **mockups** | Test data generators. `mockup-radar2keelson`. |
| **platform** | Vessel geometry publisher. `platform-geometry2keelson`. |
| **mavlink** | Direct MAVLink (ArduPilot/PX4) connector via `pymavlink`. `mavlink2keelson` (uplink). Supersedes the `keelson-connector-blueos` + `blueos-gateway` chain — talks MAVLink directly over UDP/serial/TLog instead of polling BlueOS REST. Uses the same subject contract as `keelson-connector-blueos` for drop-in replacement. |
| **labjack** | LabJack T-series (T4/T7/T8) analog voltage reader via `labjack-ljm`. Single binary `labjack2keelson`. Per-channel high-voltage scaling (resistor divider `(R1+R2)/R2` or `scale`/`offset`); publishes `analog_voltage_v` (or a configured subject). JSON config + `set_config` RPC. `--simulate` runs without hardware; the native LJM library is bundled into the Docker image. |
