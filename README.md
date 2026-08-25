# Keelson

> **NOTE**: Keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

Keelson is a start towards an maritime best practices API specification designed for building distributed maritime applications on top of the [Zenoh](https://github.com/eclipse-zenoh/zenoh) communication protocol. It is provided as free and open-source software under the Apache 2.0 License.

See the docs for details about usage -> [docs](https://RISE-Maritime.github.io/keelson)

## Repository Structure

This repository is a monorepo managed with [uv workspaces](https://docs.astral.sh/uv/concepts/workspaces/). It contains the following:

```
keelson/
├── messages/                    # Well-known subjects and protobuf message definitions
├── interfaces/                  # Generic interface definitions
├── sdks/                        # Software Development Kits
│   ├── python/                  # Python SDK (keelson)
│   └── js/                      # JavaScript SDK
├── connectors/                  # Core connector implementations
│   ├── ais/                     # AIS data connectors (keelson-connector-ais)
│   ├── camera/                  # Camera capture connector (keelson-connector-camera)
│   ├── foxglove/                # Foxglove live visualization (keelson-connector-foxglove)
│   ├── klog/                    # Klog format recording (keelson-connector-klog)
│   ├── mcap/                    # MCAP recording/replay (keelson-connector-mcap)
│   ├── mediamtx/                # MediaMTX WHEP bridge (keelson-connector-mediamtx)
│   ├── mockups/                 # Mock data generators (keelson-connector-mockups)
│   ├── nmea/                    # NMEA0183/NMEA2000 connectors (keelson-connector-nmea)
│   └── platform/                # Platform geometry publisher (keelson-connector-platform)
├── docs/                        # Documentation source
├── pyproject.toml               # Root workspace configuration
└── conftest.py                  # Shared pytest fixtures
```

### Connectors

Each connector is a self-contained Python package with its own:
- `pyproject.toml` - Package configuration and dependencies
- `bin/` - Executable scripts
- `tests/` - Unit and e2e tests

| Connector | Description |
|-----------|-------------|
| **ais** | AIS data connectors: decode/encode AIS via NMEA0183, Digitraffic MQTT |
| **camera** | Capture video frames from RTSP/USB/OpenCV sources and publish to Zenoh |
| **foxglove** | WebSocket server for real-time Foxglove visualization |
| **klog** | Record to klog binary format, convert klog to MCAP |
| **mcap** | Record Zenoh messages to MCAP format, replay MCAP files |
| **mediamtx** | Bridge for WHEP/WebRTC signaling across Zenoh networks |
| **mockups** | Generate mock radar data for testing |
| **nmea** | Bidirectional NMEA0183 and NMEA2000 connectors with CAN gateway support |
| **platform** | Publish static platform geometry information |

### Releases

- SDKs are published to their respective package repositories
- A Docker image containing all connectors is published to GitHub's container registry

## For Developers

There is a Dev Container setup for the repository which is suitable for the whole monorepo. Use it!

### Prerequisites

- [uv](https://docs.astral.sh/uv/) - Fast Python package manager
- Python 3.11+

### Setup

```bash
# Install all dependencies (SDK + all connectors)
uv sync --all-packages --group dev

# Or install only specific packages
uv sync --package keelson-connector-mcap
```

### Running Connectors

```bash
# Run connector scripts directly
uv run python connectors/mcap/bin/keelson2mcap.py --help
uv run python connectors/mcap/bin/mcap2keelson.py --help
uv run python connectors/mockups/bin/mockup-radar2keelson.py --help
```

### Testing

Tests are organized alongside each connector and use pytest with markers for test categorization.

```bash
# Run all tests
uv run pytest

# Run only unit/CLI tests (fast)
uv run pytest -m "not e2e"

# Run only end-to-end tests
uv run pytest -m e2e

# Run tests for a specific connector
uv run pytest connectors/mcap/tests/

# Run with verbose output
uv run pytest -v
```

### Documentation

Built using [`mkdocs-material`](https://squidfunk.github.io/mkdocs-material/). For local development:
* Generate docs for well-known subjects and types: `./generate_docs.sh`
* Serve the docs locally: `mkdocs serve`

### How to Make a New Release

There are three release channels. The tag names the channel, and the channel
decides which moving pointers move.

| Channel | Tag | Cut from | How |
|---|---|---|---|
| Stable | `0.6.0` | `main` | New release on GitHub |
| Integration | `0.6.0-pre.12` | `dev` | New release on GitHub, marked pre-release |
| Alpha | `0.6.0-alpha.202.dev.3` | any open PR | Actions → Release → Run workflow, with the PR number |

**Do not hand-edit the version in the SDKs** — the release workflow sets it from
the tag, so the tag and the published artifacts cannot disagree.

It used to say "update version numbers in the respective SDKs" first, and that
step is exactly what went wrong: release `0.5.4` was tagged with the SDK files
still reading `0.5.3`, both registries rejected the duplicate, and the release
shipped no SDKs at all. `docs` and `docker` succeeded, so the release page
looked normal while `npm i @rise-maritime/keelson-js@0.5.4` returned 404.

What each channel publishes:

| Channel | PyPI | npm | GHCR | Docs |
|---|---|---|---|---|
| Stable | `0.6.0` | `0.6.0` @ `latest` | `:0.6.0`, `:latest` | deployed |
| Integration | `0.6.0rc12` | `0.6.0-pre.12` @ `next` | `:0.6.0-pre.12` | — |
| Alpha | `0.6.0a202.dev3` | `0.6.0-alpha.202.dev.3` @ `pr-202` | `:0.6.0-alpha.202.dev.3`, `:pr-202` | — |

The two registries spell the same version differently by convention — semver on
npm, PEP 440 on PyPI — and the workflow does that translation for you.

An integration release must be cut from a commit on `dev`, and a stable release
from a commit on `main`. The workflow checks ancestry and refuses otherwise.

#### Developing a cross-repo feature against an unmerged keelson PR

Cut an **alpha build**. It publishes all three artifacts from a PR branch
without touching `next` or `latest`, so another repo can develop and test
against unmerged keelson changes while the PR is still in review:

```
pip:    keelson==0.6.0a202.dev3
npm:    "@rise-maritime/keelson-js": "0.6.0-alpha.202.dev.3"
docker: ghcr.io/rise-maritime/keelson:0.6.0-alpha.202.dev.3
```

While iterating, follow the moving pointers instead — `npm i
@rise-maritime/keelson-js@pr-202`, `docker pull
ghcr.io/rise-maritime/keelson:pr-202` — and pin the full version when you want
a build to stay put.

Alpha builds are addressed by **PR number**, not branch name. A branch is
mutable — `feature/checklist-subjects` named four different trees in one week —
while a PR number is permanent, and it is also what identifies the keelson half
of a cross-repo feature. The `.dev.N` serial counts up automatically per PR.

Three rules for the consuming repo:

- **Pin exactly.** `^0.6.0-alpha.202.dev.3` also matches `0.6.0-pre.11` and
  other PRs' alpha builds, so a caret silently hands you a different SDK.
  Stable ranges are safe in the other direction: neither `^0.6.0` nor
  `>=0.6.0` will ever resolve to an alpha or a pre.
- **Never ship an alpha pin on `main`.** In cross-repo work the thing that
  bites is the experiment's pin outliving the experiment. Worth a CI check.
- **Never `npm pack` an SDK and pin the tarball.** It calls itself whatever the
  deliberately-stale manifest says — a real published version with entirely
  different contents — the consuming lockfile then records that version against
  bytes nobody else has, and it goes stale the moment it is built. Cut an alpha
  build instead.

### Contribute to Keelson

- Clone repo and make a new branch with name describing the feature or change
- Make a pull request and set someone from RISE Maritime developers as reviewer
  - Tips: Make changes ready for pre-release version your contribution will be processed fast

For convenience, extension / microservices should add a [Github topic](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) `keelson-<extension_type>` to its repository to be visible in [#keelson-message](https://github.com/topics/keelson-message), [#keelson-processor](https://github.com/topics/keelson-processor) and [#keelson-connector](https://github.com/topics/keelson-connector) respectively.


