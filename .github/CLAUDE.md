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

Triggers: GitHub release published, `workflow_dispatch` (alpha or
experimental, chosen by the `channel` input), tag push (`*-alpha.*`).

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
| experimental | `0.6.0-experimental.42` | `dev` + every open PR, stacked ones included | `workflow_dispatch` with `channel=experimental` |

| Channel | PyPI | npm dist-tag | GHCR | docs |
|---|---|---|---|---|
| stable | `0.6.0` | `latest` | `:0.6.0`, `:latest` | deploy |
| integration | `0.6.0rc12` | `next` | `:0.6.0-pre.12` | skip |
| alpha | `0.6.0a202.dev3` | `pr-202` | `:0.6.0-alpha.202.dev.3`, `:pr-202` | skip |
| experimental | `0.6.0.dev42` | `experimental` | `:0.6.0-experimental.42`, `:experimental` | skip |

**python-sdk** is unguarded on purpose: a PEP 440 version (`0.6.0rc12`,
`0.6.0a202.dev3`) is already a prerelease to pip, so it is not installed
without `--pre` or an exact pin.

### The experimental channel

"Everything in flight": `origin/dev` plus every open, non-draft, same-repo PR,
merged in PR-number order. It answers "does my PR work with everyone else's?",
and nothing else — it is a moving target by construction and must never be
pinned by a consumer who wants a fixed build (that is what alpha is for).

**A stacked PR counts.** The set is every open PR whose base is `dev` *or the
head branch of another PR in the set*, so a PR opened against another PR comes
along with it. It used to be `--base dev` only, and that silently dropped them:
`navigation_control/v1` (#282, stacked on #275) was absent from experimental.1
through .5 while both PRs were open, green and mergeable, and the build
reported nothing — the omission is only a warning for a PR that was *seen* and
failed CI. A PR against `main`, or against a branch nobody has open, is still
out.

Cut by hand:

```bash
gh workflow run release.yml -f channel=experimental
```

An automatic build per push was tried first (four live builds) and dropped:
it produced a registry version per docs commit. Manual means the developer
who wants the answer decides when to ask for it.

- **Fails closed.** A PR that does not merge onto `dev` plus the PRs numbered
  before it stops the build. Nothing is published, and that PR gets one comment
  per `(dev, PR head)` pair naming the conflicting files. Oldest PR wins; the
  newer one rebases — the same rule the feature → dev flow already has.
- **A snapshot.** `dev` and every PR head are read once, in the first second
  of the run, and those exact commits are what gets waited for and merged. A
  push during the wait is not pulled in; dispatch again.
- **CI-gated per commit, before merging.** The newest CI run for each snapshot
  commit is waited for, so a dispatch straight after a push sits for one CI
  duration, and never longer, since every run waited for started at or before
  the dispatch. `dev` must be green or there is no build. A PR that is not
  green is left out: a warning annotation on the run and a `skipped` line in
  the manifest, so the dispatcher whose own PR was red does not read a green
  build as "mine works with everything". Matching on the CI workflow by commit
  is what stops the step waiting on Release itself.
- **Tested as a whole.** Individually green PRs say nothing about the
  combination. The `experimental-gate` job runs the Python and JS unit suites
  on the merged tree and blocks the publish jobs if they fail.
- **Workflow files held at dev.** `GITHUB_TOKEN` may not push a commit that
  changes `.github/workflows/`, so when a PR in the set does, the build keeps
  dev's workflow files and collapses the merges into one commit on dev. The
  manifest says `workflows held at dev: …`. A PR's CI changes are therefore
  never exercised by an experimental build — they never were: the run always
  executes the dispatched ref's `release.yml`.
- **Tree-hash gated.** A dispatch that produces a merged tree identical to the
  previous experimental tag publishes nothing.
- **Tagged like alpha.** The merge commit is pushed as `X.Y.Z-experimental.<n>`
  with the manifest (dev's commit, each PR head that went in with its title,
  each PR left out with its CI conclusion) in the tag message, so a bug report
  against `experimental.42` names an exact tree after the branches are gone.
  Serial is max existing + 1, not a count, so pruned tags never collide.
  `--cleanup=verbatim`, because `git tag -m` otherwise strips the `#275` lines
  as comments. The manifest renders on the tag's page under Releases.
- **Serialised.** A `concurrency` group queues experimental dispatches and
  never cancels one in progress — a cancel between PyPI and npm is a
  half-shipped version.
- **Fork PRs are excluded** from the tree: the job publishes with write
  credentials. Letting a fork in would be a policy change (a maintainer
  vouching, e.g. via a label), not a technical one: `refs/pull/N/head` exists
  for forks too.
- **Drafts are excluded** until marked ready for review.

Details that are easy to trip over:

- **Who can cut one:** anyone with write access, since that is what
  `workflow_dispatch` takes. Same population as alpha builds. There is no
  environment or required reviewer on the publish jobs.
- **Waits and timeouts:** a commit whose CI run has not appeared within 3
  minutes counts as `none` (not green); a run still pending after 30 minutes
  counts as `timeout` (not green). On `dev` that means no build; on a PR it
  means skipped, with a warning.
- **Testing a change to this workflow before it reaches `main`:** the workflow
  file only has to *exist* on the default branch for dispatch to be offered;
  `gh workflow run release.yml --ref <branch> ...` then runs the file from that
  branch.
- **Not a GitHub Release**, same as alpha: the Releases page is a list of
  releases. Tags are kept indefinitely for now; pruning is a follow-up.
- **Every experimental tag is a permanent registry version** on PyPI and npm.
  That was weighed against release-asset files and accepted; the price is a
  long version list, the gain is `pip install keelson==0.6.0.dev42` and
  `npm install @rise-maritime/keelson-js@experimental`.

`experimental` is the one spelling PEP 440 cannot take, so the `version` job
emits a separate `python_version` output (`0.6.0-experimental.42` →
`0.6.0.dev42`) that `python-sdk` and `docker` use. A `.dev` release sorts below
every alpha and rc, so `pip install --pre keelson` never picks it; only an
exact pin does. On npm, `experimental` sorts below `pre` and above `alpha` —
`latest` and `next` are untouched and no dependency bot proposes it.

### The ancestry guard

`version` refuses an integration tag whose commit is not an ancestor of
`origin/dev`, and a stable tag not an ancestor of `origin/main`. Alpha builds
are unmerged by definition and are not checked. An experimental build is
checked the other way round: `origin/dev` must be an ancestor of it.

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
