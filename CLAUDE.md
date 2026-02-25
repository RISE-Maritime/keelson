# Keelson

Maritime IoT monorepo by RISE-Maritime. Zenoh-based message bus with protobuf payloads for ship systems.

## Repository Layout

```
messages/          Protobuf definitions + subjects.yaml (source of truth)
interfaces/        RPC interface .proto files (5 files)
sdks/python/       Python SDK (keelson package)
sdks/js/           JavaScript/TypeScript SDK + Node-RED nodes
connectors/        9 Zenoh connectors (ais, camera, foxglove, klog, mcap, mediamtx, mockups, nmea, platform)
docker/            Single Dockerfile for all connectors
.github/workflows/ CI (ci.yml) and release (release.yml)
docs/              MkDocs documentation site
scripts/           Doc generation scripts
```

## Generated Code - DO NOT EDIT

These paths are gitignored and regenerated from `messages/`. Never edit them directly.

**Python SDK** (regen: `cd sdks/python && ./generate_python.sh`):
- `sdks/python/keelson/*_pb2.py`, `*_pb2.pyi`
- `sdks/python/keelson/Envelope_pb2.py`
- `sdks/python/keelson/payloads/` (all files)
- `sdks/python/keelson/interfaces/` (all files)
- `sdks/python/keelson/subjects.yaml` (copied from messages/)
- `sdks/python/keelson/procedures.yaml`

**JavaScript SDK** (regen: `cd sdks/js && ./generate_javascript.sh`):
- `sdks/js/keelson/Envelope.ts`
- `sdks/js/keelson/subjects.json`
- `sdks/js/keelson/typeRegistry.ts`
- `sdks/js/keelson/payloads/` (all files)
- `sdks/js/keelson/interfaces/` (all files)
- `sdks/js/keelson/google/` (all files)

**Docs** (regen: `./generate_docs.sh`):
- `docs/subjects-and-types.md`
- `docs/interfaces.md`
- `docs/payloads/`, `docs/interfaces/`

If tests fail with `_pb2` import errors or missing subjects, regenerate the SDK code first.

## Package Management

- **Python**: `uv` workspace. `uv sync` for SDK, `uv sync --all-packages` for connectors.
- **JavaScript**: `npm` in `sdks/js/`. Needs `uv sync --group dev` first (for protoc).

## Key Commands

```bash
# Python tests
uv run pytest -vv sdks/python/tests/           # SDK tests
uv run pytest -vv -m "not e2e" connectors/     # Connector unit tests
uv run pytest -vv -m e2e connectors/           # Connector e2e tests
uv run pytest -vv connectors/mcap/tests/       # Single connector

# Linting
uv run ruff check .
uv run black --check sdks/python --extend-exclude _pb2.py
uv run black --check connectors

# JavaScript tests
cd sdks/js && npm test                          # Compile + Jest + Mocha

# Docker
docker build -f docker/Dockerfile -t keelson .
```

## Python Style

- **Black** + **Ruff**, Python >= 3.11, snake_case
- Exclude `_pb2.py` from Black: `--extend-exclude _pb2.py`
- Test markers: `@pytest.mark.unit`, `@pytest.mark.e2e`, `@pytest.mark.slow`

## Git Workflow

- Branches: feature -> dev -> main
- Version tracked in `sdks/python/pyproject.toml` and `sdks/js/package.json` (currently `0.5.0`)

## Zenoh Key Format

```
{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}       # Pub/Sub
{base_path}/@v0/{entity_id}/@rpc/{procedure}/{responder_id}    # RPC
{base_path}/@v0/{entity_id}/pubsub/*/{source_id}               # Liveliness
```

## Envelope Pattern

All data on the bus is wrapped in an `Envelope` (see `messages/Envelope.proto`):
1. Serialize the domain payload (e.g., `TimestampedFloat`) to bytes
2. Create `Envelope(enclosed_at=now, payload=serialized_bytes)`
3. Serialize the Envelope and publish

To read: deserialize Envelope, then deserialize `payload` bytes using the type from the subject registry.

## Common Mistakes

1. **Editing generated files** - Files under `payloads/`, `interfaces/`, and `*_pb2*` are generated. Edit the `.proto` source in `messages/` instead.
2. **Forgetting to regenerate** - After changing `.proto` files or `subjects.yaml`, run both `generate_python.sh` and `generate_javascript.sh`.
3. **Skarv test pollution** - The `skarv` library caches state in module-level dicts and `lru_cache`. Connector tests (especially nmea, ais) must clear skarv state between tests or cross-test pollution occurs. See `connectors/CLAUDE.md` for the fixture pattern.
4. **subjects.yaml without matching proto** - Every subject references a protobuf type. Adding a subject for a type that doesn't exist will cause runtime errors.
5. **Running tests without generating** - CI always runs `generate_python.sh` before tests. Locally you must do the same after a fresh clone or proto change.
