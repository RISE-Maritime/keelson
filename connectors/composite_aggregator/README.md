# composite_aggregator connector

A Layer 3 aggregation policy: consumes `entity_health` and publishes
`operational_authority` under its own `source_id`.

The policy is **compensatory** — a mean of per-source health, each source
discounted by how much of it could be assessed — with **non-compensatory
ceilings** on top: a component marked essential caps authority at its own
score, and no amount of healthy unrelated equipment buys the cap back.

Beside it, [`warrant_aggregator`](../warrant_aggregator/) derives the same
subject from a graph of claims instead of a score. Both publish
`operational_authority`, both self-identify with `policy_id`, and they are
allowed to disagree. See [docs/health-monitoring.md](../../docs/health-monitoring.md).

## Usage

```bash
composite_aggregator2keelson \
  --realm test-realm \
  --entity-id test-vessel \
  --source-id composite \
  --config example-policy.json
```

## The policy file

See [`example-policy.json`](example-policy.json). Every key is optional; an
absent key takes the shipped default, so retuning one threshold does not
require restating the ladder and risking a transcription error in the part you
did not mean to touch.

```json
{
  "score_by_level": {"NOMINAL": 1.0, "DEGRADED": 0.5, "CRITICAL": 0.0,
                     "INACTIVE": 0.0, "UNKNOWN": 0.0},
  "ladder": [
    {"min_score": 0.85, "level": "FULL_AUTONOMOUS"},
    {"min_score": 0.65, "level": "ASSISTED_AUTONOMOUS"},
    {"min_score": 0.45, "level": "REMOTE_CONTROLLED"},
    {"min_score": 0.25, "level": "SUPERVISED_REMOTE"},
    {"min_score": 0.0,  "level": "MINIMAL_SAFE_MODE"}
  ],
  "hysteresis_margin": 0.05,
  "essential": [
    {"source": "gnss_main", "subject": "location_fix"}
  ]
}
```

Rung order does not matter — rungs are sorted best-first on load, because the
evaluation takes the first matching rung as the highest.

`essential` entries name a `source` and optionally a `subject`. Naming only
the source is shorthand for a source that genuinely is one indivisible
requirement; naming a subject is the precise form, and it exists because a
GNSS source's position fix can be a hard prerequisite while its four ancillary
diagnostics are not — capping on the whole source there would let an unread
diagnostic veto the vessel. Marking a source does not imply its subjects and
marking a subject does not imply its source; each is capped on its own terms.

`hysteresis_margin` is a **burden-of-proof rule, not display smoothing**. To
climb, the score must clear the higher threshold *by the margin*; to fall, it
must drop below the current level's threshold by the margin. Claiming more
autonomy is the direction that can hurt someone, so it is the direction made
harder to take on marginal evidence. Setting it to `0` deletes that asymmetry,
not just the jitter.

**What is deliberately not configurable.** Which levels are *scored* at all
(`NOT_ADVERTISED` is excluded — a watch-config typo is not a fact about the
vessel) and which count as *assessed* for the coverage discount (a known
failure is evidence, not missing evidence) are semantic invariants rather than
policy choices. Both encode what a level means, and both guard a specific bug
documented in `authority.py`.

## Provenance

Every determination carries `policy_id` and `policy_config_digest`, the latter
a SHA-256 over the policy file's bytes — so an auditor holding the file
reproduces it with `sha256sum` and nothing else. That is only sound because
this connector cannot be reconfigured at runtime: the file is the policy for
the life of the process.

## Tests

```bash
uv run pytest -vv -m "not e2e" connectors/composite_aggregator/
uv run pytest -vv -m e2e connectors/composite_aggregator/
```
