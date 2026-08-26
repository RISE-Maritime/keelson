# Keelson

Maritime IoT monorepo by RISE-Maritime. Zenoh-based message bus with protobuf payloads for ship systems.

## Repository Layout

```
messages/          Protobuf definitions + subjects.yaml, qos.yaml, interfaces.yaml (source of truth)
interfaces/        RPC interface .proto files (16 files, one protobuf package each)
sdks/python/       Python SDK (keelson package)
sdks/js/           JavaScript/TypeScript SDK + Node-RED nodes
connectors/        19 Zenoh connectors (ais, camera, composite_aggregator, entity_health,
                   foxglove, hand_controller, iso22133, klog, labjack, mavlink, mcap,
                   mediamtx, mockups, network_manager, nmea, platform, rtcm, tak,
                   warrant_aggregator)
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
- `sdks/python/keelson/payloads/` (all files, incl. the payload FileDescriptorSet)
- `sdks/python/keelson/interfaces/` (all files, incl. the interface FileDescriptorSet and the generated `__init__.py` re-exporting `keelson.interfaces_registry`)
- `sdks/python/keelson/subjects.yaml` (copied from messages/)
- `sdks/python/keelson/qos.yaml` (copied from messages/)
- `sdks/python/keelson/interfaces.yaml` (copied from messages/)

**JavaScript SDK** (regen: `cd sdks/js && ./generate_javascript.sh`):
- `sdks/js/keelson/Envelope.ts`
- `sdks/js/keelson/subjects.json`
- `sdks/js/keelson/qos.json`
- `sdks/js/keelson/interfaces.json`
- `sdks/js/keelson/typeRegistry.ts`
- `sdks/js/keelson/payloads/` (all files)
- `sdks/js/keelson/interfaces/` (all files, incl. the generated `serviceRegistry.ts`)
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

- Branches: feature -> dev -> main. `dev` is the integration branch: feature PRs
  target it, conflicts between them are resolved on the feature branch, and the
  batch is promoted to `main` with a single `dev -> main` PR.
- After anything lands on `main` (a release, a hotfix), merge `main` back into `dev`.
  A branch merged only one way drifts.
- Three release channels, named by the tag: stable `0.6.0` from `main`,
  integration `0.6.0-pre.12` from `dev`, alpha `0.6.0-alpha.<pr>.dev.<n>` from
  any open PR. The release workflow enforces the first two by ancestry. See
  `.github/CLAUDE.md` for what each channel publishes where.
- **Never hand-edit the version in `sdks/python/pyproject.toml` or
  `sdks/js/package.json`.** Both are deliberately stale; `release.yml` sets the
  real version from the tag. Hand-bumping them "in lockstep" is what broke
  0.5.4 — it was tagged with the files still reading 0.5.3, both registries
  rejected the duplicate, and the release shipped no SDKs at all.
- A consumer that needs an unmerged keelson change — typically a cross-repo
  feature — cuts an **alpha build** from the PR and pins it exactly. Do not
  `npm pack` a tarball and pin that, and do not cut a `-pre.N` from a feature
  branch: `-pre.N` is the integration line and regressing it breaks `next` for
  everyone.

## Zenoh Key Format

```
{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}                       # Pub/Sub
{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/{procedure}/{source_id} # RPC
{base_path}/@v0/{entity_id}/*/{source_id}                                      # Liveliness: source-level
{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}                       # Liveliness: subject-level
{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/*/{source_id}           # Liveliness: RPC interface
```

RPC interfaces are versioned (`v1`, `v2`, ...) and registered in
`messages/interfaces.yaml`. Liveliness is three-tier (see
`docs/protocol-specification.md` §5); producing connectors declare
source + per-subject tokens via `keelson.scaffolding.declare_liveliness`,
RPC servers get their interface token from `keelson.scaffolding.serve_rpc`.
Pure consumers (sinks) declare no tokens.

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
6. **Editing a `connectors/*/requirements.txt` without re-locking** — see Dependency Management below.

## Dependency Management

`uv.lock` is the single source of truth for every third-party version. `uv sync` drives local venvs and CI test jobs; `uv export` produces `requirements-prod.txt`, which the Dockerfile installs with `--no-deps`. No implicit upgrades on any path.

- `connectors/*/requirements.txt` — declared ranges; edit to add/change a dep.
- `uv.lock` — generated by `uv lock`.
- `requirements-prod.txt` — generated by `uv export`; never edit by hand.

**Bump a dep:**

```bash
uv lock --upgrade-package <name>      # or edit a requirements.txt, then uv lock
uv export --frozen --format requirements-txt \
    --no-emit-workspace --no-hashes --no-dev -o requirements-prod.txt
```

The CI `lint` job re-runs the export and fails on drift between `uv.lock` and `requirements-prod.txt`. It does **not** watch stale venvs or dev-deps drift — run `uv sync --all-packages --group dev` after pulling main.
