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
| **test-javascript-sdk** | Node 24.x, `npm test` in sdks/js |
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

### Channels

The tag names the channel, and `version` classifies it. There is no longer any
dependence on the release's `prerelease` checkbox: it is a human input that can
disagree with the tag, it is absent on the two non-release triggers, and an
absent `prerelease` compares equal to `false` in a GitHub expression — so a tag
push would have read as *stable* and deployed the docs.

| Channel | Tag | Cut from | Trigger |
|---|---|---|---|
| stable | `0.6.0` | `main` | `release: published` |
| integration | `0.6.0-pre.12` | `dev` | `release: published` (pre-release) |
| alpha | `0.6.0-alpha.202.dev.3` | any open PR | `workflow_dispatch` with the PR number |

| Channel | PyPI | npm dist-tag | GHCR | docs |
|---|---|---|---|---|
| stable | `0.6.0` | `latest` | `:0.6.0`, `:latest` | deploy |
| integration | `0.6.0rc12` | `next` | `:0.6.0-pre.12` | skip |
| alpha | `0.6.0a202.dev3` | `pr-202` | `:0.6.0-alpha.202.dev.3`, `:pr-202` | skip |

**python-sdk** is unguarded on purpose: a PEP 440 version (`0.6.0rc12`,
`0.6.0a202.dev3`) is already a prerelease to pip, so it is not installed
without `--pre` or an exact pin.

### The ancestry guard

`version` refuses an integration tag whose commit is not an ancestor of
`origin/dev`, and a stable tag not an ancestor of `origin/main`. Alpha builds
are unmerged by definition and are not checked.

This is not hypothetical tidiness. Between `0.6.0-pre.5` and `0.6.0-pre.12`,
seven of twelve prereleases were cut from unmerged feature branches. npm's
`next` gained and lost the checklist payloads four times, so a consumer
following the integration line watched protocol types appear, vanish and
reappear. The convention was written down and violated within a day of being
documented — hence a check rather than a sentence.

`target_commitish` cannot do this job: `0.6.0-pre.6` recorded a bare SHA, so it
does not reliably name a branch. The check is `git merge-base --is-ancestor`,
which is why `version` checks out with `fetch-depth: 0`.

### Why alpha builds are numbered, not named

`0.6.0-checklist.3` is valid semver and a valid Docker tag, but PEP 440 rejects
it outright — there is no room for a word, only `a`/`b`/`rc` plus integers. A
scheme PyPI rejects is a scheme where the three registries stop agreeing on what
a build is called, which is the 0.5.4 failure in a new dress. A PR number is a
number, so `0.6.0-alpha.202.dev.3` normalises cleanly to `0.6.0a202.dev3` and
one string identifies the build everywhere.

It is also the better key: a branch is mutable and gets deleted on merge, while
a PR number is permanent and names the keelson half of a cross-repo feature.

The `.dev.N` serial is a count of existing tags for that PR. A force-push means
`dev.3` and `dev.4` can be unrelated trees, so the tag message records the
commit.

Alpha tags are pushed with `GITHUB_TOKEN`, which by design does not re-trigger
the `push: tags` route — the publish happens in the dispatching run. That route
exists for hand-pushed tags, and because `workflow_dispatch` is only available
from the default branch, it is also the only way to test a change to this
workflow before it is merged.

### npm trusted publishing (OIDC)

`@rise-maritime/keelson-js` authenticates to npmjs.com with a **trusted publisher**
bound to this repo and `release.yml` — there is no `NPM_TOKEN` in the publish step,
and adding one back only hides an OIDC failure until the token itself goes stale.
Two things keep it working:

- `permissions: id-token: write` at the top of `release.yml`
- **npm >= 11.5.1**, which is where OIDC support landed

The second is why the `javascript-sdk` job pins Node 24 (bundles npm 11.17) rather
than Node 22 (bundles npm 10.9, no OIDC). npm without OIDC support does not error —
it falls back to token auth and the registry replies `404 Not Found - PUT`, naming
neither npm nor auth. That cost releases 0.6.0-pre.1 and 0.6.0-pre.2, so the job now
asserts the npm version up front. Use the npm that ships with Node; never
`npm install -g npm@latest`, which is the other half of the same story.

## Adding a New Connector to CI

1. Add test path to root `pyproject.toml` testpaths (picked up by unit + e2e jobs automatically)
2. Add Docker smoke test in `ci.yml` docker-build job: `docker run --rm keelson "{binary-name} --help"`
3. Add Dockerfile lines: install requirements, copy bin/ scripts

## Key Details

- **uv**: installed via `astral-sh/setup-uv@v7`
- **Node**: 24.x via `actions/setup-node@v4` — pinned by the npm it bundles, see
  npm trusted publishing below
- **Docker smoke tests**: run `--help` on every binary to verify they're accessible and parseable
- **JS SDK**: needs both `uv sync --group dev` (for protoc) and `npm ci` (for ts-proto)
- **Docs release**: installs protodot + graphviz for proto diagrams
