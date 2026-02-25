# Connectors

9 Zenoh connectors that ingest/export data between external systems and the Keelson bus.

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
4. Add to `docker/Dockerfile` (requirements install + bin copy)
5. Add smoke test in `.github/workflows/ci.yml` docker-build job
6. Add testpath to root `pyproject.toml` `[tool.pytest.ini_options]`
7. Write tests with conftest.py following patterns above

## Connector-Specific Notes

| Connector | Notes |
|---|---|
| **mcap** | MCAP file recording/replay. `keelson2mcap.py` (712 lines) supports rotation. Also `mcap2keelson.py` (replay) and `mcap-tagg.py` (annotation). |
| **nmea** | 5 binaries: `nmea01832keelson`, `keelson2nmea0183`, `n2k2keelson`, `keelson2n2k`, `n2k-cli`. Uses skarv for message routing. |
| **ais** | 3 binaries: `ais2keelson`, `keelson2ais`, `digitraffic2keelson`. Uses pyais, geopy, paho-mqtt. Skarv for routing. |
| **camera** | OpenCV-based video capture. Single binary `camera2keelson`. |
| **klog** | Binary KLOG format recording. `keelson2klog` + `klog2mcap` converter. |
| **foxglove** | Foxglove Studio live view bridge. Single binary `keelson2foxglove`. |
| **mediamtx** | WHEP/WebRTC proxy. Single binary `mediamtx-whep`. |
| **mockups** | Test data generators. `mockup-radar2keelson`. |
| **platform** | Vessel geometry publisher. `platform-geometry2keelson`. |
