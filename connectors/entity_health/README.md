# entity_health connector

Watches a declarative set of `(source, subject)` pairs on the bus,
measures the publication rate and validates payload content, and
publishes a `keelson.EntityHealth` message on the `entity_health`
subject.

The proto layout is `EntityHealth → SourceHealth → SubjectHealth`: an
**entity** is the system being monitored (a vessel, a drone), a
**source** is one device on it (a GNSS receiver, a battery monitor), and
each source publishes one or more **subjects** (e.g. `location_fix`,
`battery_voltage_v`).

The active configuration can be replaced at runtime via the
`Configurable` RPC interface (`get_config` / `set_config`).

## Usage

```bash
entity_health2keelson \
  --realm test-realm \
  --entity-id test-vessel \
  --source-id health \
  --config example-config.json
```

## Configuration

See [`example-config.json`](example-config.json) for a full example.

```json
{
  "publish_rate_hz": 1.0,
  "realm": "test-realm",
  "entity_id": "test-vessel",
  "sources": [
    {
      "name": "gnss_main",
      "subjects": [ ... ]
    }
  ]
}
```

Top-level fields:

| Field             | Description                                                       |
|-------------------|-------------------------------------------------------------------|
| `publish_rate_hz` | Rate at which the connector emits `EntityHealth` (default 0.1 Hz) |
| `realm`           | Optional. Realm to use when monitoring sources. Defaults to `--realm`     |
| `entity_id`       | Optional. Entity to monitor. Defaults to `--entity-id`. Override to watch a different entity than the one this connector publishes its own output on |
| `sources`         | Required. One entry per device to monitor                         |

Each `sources` entry has:

| Field      | Description                                            |
|------------|--------------------------------------------------------|
| `name`     | The publisher's `source_id` — emitted in `SourceHealth.name` |
| `subjects` | One entry per subject the source publishes             |

Each `subjects` entry defines what to expect from one (source, subject)
pair. The connector subscribes to the exact key
`{realm}/@v0/{entity_id}/pubsub/{subject}/{source}` — wildcards are
**not supported**: each entry maps to exactly one publisher.

| Field                | Description                                                        |
|----------------------|--------------------------------------------------------------------|
| `name`               | Subject name (must exist in `subjects.yaml`); emitted in `SubjectHealth.name` |
| `inactive_after_s`   | Silence longer than this → INACTIVE (default 10s)                  |
| `window_s`           | Sliding window over which the publication rate is measured (default 10s) |
| `publication_rate_hz`         | Tiered bands applied to the observed publication rate (see below)  |
| `publication_rate_default_level` | Level used when the observed rate matches no band (default CRITICAL) |
| `require_liveliness` | If `true` (default), liveliness gates the result — see [Liveliness and the three-tier model](#liveliness-and-the-three-tier-model) below |
| `content_rules`      | List of content-value checks (see below)                           |

### Rate bands

The connector measures the publication rate over a sliding window and
maps the observed Hz value through `publication_rate_hz` the same way content
rules work: bands are checked best→worst, first match wins, and if
nothing matches the level falls back to `publication_rate_default_level`. Omitting
`publication_rate_hz` disables the rate check entirely.

```json
"publication_rate_hz": [
  {"level": "NOMINAL",  "min": 8.0, "max": 12.0},
  {"level": "DEGRADED", "min": 4.0, "max": 20.0}
],
"publication_rate_default_level": "CRITICAL"
```

For the example above, a 10 Hz target with ±20% acceptable:

| Observed rate | Level     |
|---------------|-----------|
| `10.0`        | NOMINAL   |
| `6.0`         | DEGRADED  |
| `1.0`         | CRITICAL (falls through to `publication_rate_default_level`) |

### Liveliness and the three-tier model

Keelson liveliness has three orthogonal tiers (see `messages/protocol-specification.md`
§5 and `keelson.scaffolding.liveliness`):

- **Source-level** — `{realm}/@v0/{entity}/*/{source}` — "this process is
  present on the bus as a producer", declared once per producing process.
- **Pubsub subject-level** — `{realm}/@v0/{entity}/pubsub/{subject}/{source}`
  — "this source is configured/wired to publish `{subject}`" (capability,
  not activity — the token stays up even while the subject is momentarily
  silent).
- **Legacy coarse token** — `{realm}/@v0/{entity}/pubsub/*/{source}` — the
  pre-three-tier token some connectors still declare during the transition
  window. It only conveys source-level presence; it says nothing about
  which subjects a source publishes.

The connector watches each **source** (not each subject) for liveliness:
one subscription on the `pubsub/*/{source}` pattern (which — via Zenoh's
wildcard matching — receives both the legacy coarse token and every
concrete subject-level token of that source) and one on the exact
source-level token key. Both use `history=True`, so already-present
tokens are picked up at startup. The result is shared by every subject
Evaluator for that source and drives this state machine (only when
`require_liveliness` is `true`; see below for `false`):

| Source presence | Subject advertised? | Result |
|---|---|---|
| absent | — | **UNKNOWN** — no information at all |
| present | yes (subject-level token live) | full evaluation (activity gate → rate/content checks), same as below |
| present | no, but the source advertises ≥1 *other* subject | **NOT_ADVERTISED** — a resolved negative: the source has adopted three-tier liveliness and simply isn't configured to publish this subject. Almost always a config typo (wrong subject name or `source_id`) |
| present | source advertises no subjects at all (legacy source — only the coarse token is visible) | transitional fallback: full evaluation, same as "advertised" — can't distinguish "not configured" from "configured but never publishes" without a subject-level token, so it falls back to activity-based detection like before three-tier existed |

And within "full evaluation":

| Data                                            | Level    |
|--------------------------------------------------|----------|
| no samples yet, or silent > `inactive_after_s`   | INACTIVE |
| rate / content OK                                | NOMINAL  |

`NOT_ADVERTISED` is a *diagnostic*, not a fault level: it stays fully
visible on the affected `SubjectHealth` (and in the startup warning), but
is excluded from the source/entity worst-of rollups so a config typo or
stale watch can never mask a genuine `CRITICAL` on a correctly watched
sibling subject.

Set `"require_liveliness": false` on an expectation when the data source
never declares any liveliness token (e.g. MCAP replays, or AIS feeds
bridged from outside the Keelson bus). Liveliness is then bypassed
entirely — the connector falls back to sample-based activity detection
only, exactly as if the expectation had no liveliness concept at all.

### Content rules

Content rules check a field on the decoded payload. Each rule has a
list of `bands` that map value ranges (or equality matches) to health
levels. Bands are checked best→worst; the first matching band wins.
If no band matches, `default_level` is used (default: `CRITICAL`).

```json
{
  "field": "value",
  "bands": [
    {"level": "NOMINAL",  "min": 12.0, "max": 14.5},
    {"level": "DEGRADED", "min": 11.0, "max": 15.0},
    {"level": "CRITICAL", "min": 10.0, "max": 16.0}
  ],
  "default_level": "CRITICAL"
}
```

For the example above:

| Voltage reading | Level     |
|-----------------|-----------|
| `13.2`          | NOMINAL   |
| `11.5`          | DEGRADED  |
| `15.5`          | CRITICAL  |
| `9.0`           | CRITICAL (falls through to `default_level`) |

Bands typically nest (NOMINAL ⊂ DEGRADED ⊂ CRITICAL) but this isn't
enforced — they are simply checked in best→worst order. `min` and `max`
are both optional; omit either to make the band one-sided.

**Equality / set match** — bands can also match on equality instead of
a numeric range. Use `equals` with a scalar (string, number, bool) or a
list of scalars. `equals` takes precedence over `min`/`max`.

```json
{
  "field": "quality",
  "bands": [
    {"level": "NOMINAL",  "equals": ["RTK_FIXED", "RTK_FLOAT"]},
    {"level": "DEGRADED", "equals": "DGPS"},
    {"level": "CRITICAL", "equals": "INVALID"}
  ],
  "default_level": "DEGRADED"
}
```

This is handy for enum-like fields (GNSS fix quality, navigation
status) and for boolean flags (`{"equals": false, "level": "CRITICAL"}`
on an `is_healthy` field).

Valid `level` names: `NOMINAL`, `DEGRADED`, `CRITICAL`, `INACTIVE`
(prefix `HEALTH_` is also accepted).

### Combining rate and content checks

For each subject, the evaluator emits one `CheckResult` per check that
ran (the standard `activity` and `publication_rate` checks plus one per
configured content rule), and `SubjectHealth.level` is the **worst**
level across them. The per-check explanation lives on
`SubjectHealth.checks[i].detail`.

Per-source rollup: `SourceHealth.level` is the worst level among the
source's subjects. The overall `EntityHealth.level` is the worst level
among all sources.

### Health levels

| Level             | When                                                              |
|-------------------|--------------------------------------------------------------------|
| `NOMINAL`         | Liveliness present, rate in NOMINAL band, all content rules pass  |
| `DEGRADED`        | A rate or content band resolved to DEGRADED                       |
| `CRITICAL`        | A rate or content band resolved to CRITICAL                       |
| `INACTIVE`        | Alive but silent longer than `inactive_after_s`                   |
| `NOT_ADVERTISED`  | Source present, but doesn't advertise a subject-level token for this subject — three-tier adopters only; see [Liveliness and the three-tier model](#liveliness-and-the-three-tier-model) |
| `UNKNOWN`         | `require_liveliness` is true and the source has no liveliness presence at all |

Worst-of ranking of the fault levels (worst → best): `INACTIVE` >
`CRITICAL` > `DEGRADED` > `NOMINAL`. The two *diagnostic* levels are
excluded from worst-of rollups: `UNKNOWN` means "no information", and
`NOT_ADVERTISED` means "the watch config is wrong" — neither is a fault
of the monitored system, and neither may mask one on a sibling subject.
A rollup consisting *only* of diagnostics reports `NOT_ADVERTISED` if
any subject resolved to it (the more informative of the two), otherwise
`UNKNOWN`. The overall `EntityHealth.level` is the worst level among all
sources.

## Keeping data local: monitoring sensors that don't leave the entity

A common case: a sensor (e.g. a lidar on the entity) publishes
high-bandwidth data inside the entity, but you only want the outside
world to see an aggregated health summary — not the raw stream.

This works naturally because the entity_health connector subscribes at
the **local** Zenoh peer, so the sensor's traffic only needs to be
visible inside the entity's mesh, not outside it.

### Architecture

```
┌────────────────── entity ─────────────────┐
│                                            │
│  lidar driver ─► entity/internal/.../laser_scan/lidar_front
│                          │                 │
│                          ▼                 │
│              entity_health connector       │
│                          │                 │
│                          ▼                 │
│       entity/external/.../entity_health/health
│                          │                 │
└──────────────────────────┼─────────────────┘
                           │
                egress router / bridge
                (allows only entity/external/**)
                           │
                           ▼
                    fleet operator
```

Use two namespaces (or two Keelson realms):

- `entity/internal/...` — everything produced locally; **never bridged**.
- `entity/external/...` — only what the outside world should see (the
  EntityHealth summary, possibly a low-rate position, etc.).

The "stays inside" enforcement is a **Zenoh routing concern**, not an
entity_health concern. Two common patterns:

1. **Two routers**: an internal router on the entity with no WAN
   `connect`, plus a bridge router that connects to both meshes with
   `access_control` ACLs allowing only `entity/external/**` across.
2. **One router with ACLs**: a single router on the entity whose
   `access_control` JSON5 config denies any non-`entity/external/**` key
   from being forwarded over its WAN endpoint.

### Connector config

The entity_health connector runs **inside the entity** and watches the
internal namespace. Run it with `--realm entity/external` so its own
output lands on the external namespace and gets bridged out, and use
the config-level `realm`/`entity_id` to point the *monitored* keys at
the internal namespace:

```json
{
  "publish_rate_hz": 1.0,
  "realm": "entity/internal",
  "entity_id": "entity1",
  "sources": [
    {
      "name": "lidar_front",
      "subjects": [
        {
          "name": "laser_scan",
          "inactive_after_s": 1.0,
          "publication_rate_hz": [
            {"level": "NOMINAL",  "min": 9.0, "max": 11.0},
            {"level": "DEGRADED", "min": 5.0, "max": 15.0}
          ],
          "publication_rate_default_level": "CRITICAL",
          "require_liveliness": true
        }
      ]
    }
  ]
}
```

The operator only ever sees the aggregated `EntityHealth` message —
the raw `laser_scan` payloads stay inside the entity.

> **Note on lidar content checks**: today's content rules read a single
> scalar field via `getattr`. They don't aggregate over `repeated`
> fields like `LaserScan.ranges`. For pure rate + liveliness monitoring
> the example above works as-is. To check scan content (e.g. "≥100
> valid returns per scan") you'd publish a derived scalar from the
> driver, or extend the evaluator with a small aggregation primitive.

## Runtime reconfiguration

Send a Zenoh GET on the `Configurable` `set_config` RPC key with a JSON
body matching the schema above. Subscribers whose derived key expression
changed (because the source, subject, realm, or entity_id changed) are
re-declared; unchanged (source, subject) pairs are kept and their
sample history is preserved across the reconfiguration.

## Tests

```bash
uv run pytest -vv connectors/entity_health/tests/                # all
uv run pytest -vv -m "not e2e" connectors/entity_health/tests/   # unit only
uv run pytest -vv -m e2e      connectors/entity_health/tests/    # e2e only
```
