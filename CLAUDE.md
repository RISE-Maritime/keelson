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
6. **Editing a `connectors/*/requirements.txt` without re-locking** - The docker image installs from `requirements-prod.txt`, a frozen export of `uv.lock`. Bumping or adding a dep requires running `uv lock && uv export --frozen --format requirements-txt --no-emit-workspace --no-hashes --no-dev -o requirements-prod.txt` and committing all three changed files (`connectors/X/requirements.txt`, `uv.lock`, `requirements-prod.txt`). CI's `lint` job rejects PRs where these have drifted.

## Dependency Management

`uv.lock` is the single source of truth for every third-party Python version. Three consumers read from it, no implicit upgrades anywhere:

```
            uv.lock  (single source of truth)
              │
   ┌──────────┼──────────┐
   ▼          ▼          ▼
 uv sync   uv sync    uv export
 (local    (CI test   (CI lint
  dev)      jobs)      + Dockerfile)
  → venv   → venv     → requirements-prod.txt
                       → docker image
```

Files involved:

- **`connectors/*/requirements.txt`** — declared, range-pinned source (`pytak>=6.0`). Read by `uv` when resolving the workspace. Edit these to add or change a dep.
- **`uv.lock`** — fully-resolved transitive graph with exact versions, hashes, and per-platform wheels. Generated by `uv lock`. Drives `uv sync` for local venvs and CI test jobs.
- **`requirements-prod.txt`** — flat, exact-pinned `pip install`-compatible export of `uv.lock` (with `--no-emit-workspace --no-dev`). Generated by `uv export`. Drives the docker image install with `pip install --no-deps`. Never edit by hand.

This means new upstream releases **do not** flow into either local venvs or the docker image automatically — they require an explicit `uv lock --upgrade-package <name>` (or `uv lock --upgrade` for all). The trade-off is deterministic, identical installs in exchange for explicit dep bumps. Renovate/Dependabot or a scheduled `uv lock --upgrade` cadence are reasonable follow-ups if patch-uptake matters.

### Bumping a dep

```bash
# Pick one:
uv lock --upgrade-package <name>      # bump within current range
$EDITOR connectors/<name>/requirements.txt && uv lock   # change the range

# Then always:
uv export --frozen --format requirements-txt \
    --no-emit-workspace --no-hashes --no-dev \
    -o requirements-prod.txt

git add uv.lock requirements-prod.txt connectors/*/requirements.txt
```

If you skip the `uv export` step, CI's `lint` job will fail with a copy-pasteable fix command.

### CI drift check — what it catches and what it doesn't

CI's `lint` job runs the export and `git diff --exit-code requirements-prod.txt`. It catches:

1. `connectors/X/requirements.txt` edited without re-locking.
2. `uv.lock` bumped without re-exporting.
3. A teammate landed a lock bump on main and your branch is behind.

It does **not** catch:

- A stale local venv. If you haven't `uv sync`'d in a while, your venv lags behind `uv.lock` — re-sync before working. There's no CI signal for this; you'd notice as "tests pass locally but fail in CI" or vice versa.
- Dev-deps drift. Dev deps (`black`, `ruff`, `mypy`, `pytest`, `pexpect`, etc.) are in `[dependency-groups] dev` at the repo root, pinned in `uv.lock` but **not** in `requirements-prod.txt` (correctly — they don't ship in the runtime image). They stay consistent across teammates via `uv sync --group dev`; the drift check doesn't watch them.

### After pulling main

```bash
uv sync --all-packages --group dev
```

Picks up any lock changes a teammate landed. Skip this and your local venv silently lags.
