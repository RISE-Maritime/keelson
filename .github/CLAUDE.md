# CI/CD

Two workflow files: `ci.yml` (continuous integration) and `release.yml` (publishing).

## CI Pipeline (ci.yml)

Triggers: push to main or dev, all pull requests.

```
lint ──────────────┬── test-sdk [matrix: 3.11, 3.12, 3.13]
                   │        │
                   │   test-connectors-unit
                   │        │
                   │   test-connectors-e2e
                   │
                   └── docker-build (needs: lint, test-sdk, test-connectors-unit)

test-javascript-sdk (independent, no dependencies)
```

### Jobs

| Job | What it does |
|---|---|
| **lint** | `ruff check .` + `black --check` (sdks/python + connectors, excludes `_pb2.py`) |
| **test-sdk** | Python matrix (3.11/3.12/3.13), `pytest sdks/python/tests/` |
| **test-connectors-unit** | `pytest -m "not e2e" connectors/` |
| **test-connectors-e2e** | `pytest -m e2e connectors/` |
| **test-javascript-sdk** | Node 20.x, `npm test` in sdks/js |
| **docker-build** | Build image, smoke test every binary with `--help` |

### Critical: Every Python Job Must Regenerate SDK Code

Every job that runs Python tests does this first:
```bash
uv sync --group dev         # or --all-packages for connectors
cd sdks/python && ./generate_python.sh
```

Without this, `_pb2` imports will fail.

## Release Pipeline (release.yml)

Triggers: GitHub release published.

| Job | Target |
|---|---|
| **python-sdk** | Build wheel, publish to PyPI via `pypa/gh-action-pypi-publish` |
| **javascript-sdk** | `npm publish --provenance --access public` (tag `next` for prereleases) |
| **docker** | Multi-platform build (linux/amd64), push to `ghcr.io/rise-maritime/keelson` |
| **docs** | `mkdocs gh-deploy --force` to GitHub Pages (stable releases only) |

### Prereleases

Prereleases are cut from `dev`, stable releases from `main`. Marking the GitHub
release as a prerelease changes three jobs:

- **javascript-sdk** publishes under the `next` dist-tag instead of `latest`
- **docker** pushes only `:<tag_name>`, leaving `:latest` on the last stable release
- **docs** is skipped entirely

**python-sdk** is unguarded on purpose: a PEP 440 version (`0.6.0rc1`) is already a
prerelease to pip, so it is not installed without `--pre` or an exact pin.

## Adding a New Connector to CI

1. Add test path to root `pyproject.toml` testpaths (picked up by unit + e2e jobs automatically)
2. Add Docker smoke test in `ci.yml` docker-build job: `docker run --rm keelson "{binary-name} --help"`
3. Add Dockerfile lines: install requirements, copy bin/ scripts

## Key Details

- **uv**: installed via `astral-sh/setup-uv@v7`
- **Node**: 20.x via `actions/setup-node@v4`
- **Docker smoke tests**: run `--help` on every binary to verify they're accessible and parseable
- **JS SDK**: needs both `uv sync --group dev` (for protoc) and `npm ci` (for ts-proto)
- **Docs release**: installs protodot + graphviz for proto diagrams
