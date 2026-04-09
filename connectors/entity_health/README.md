# entity_health connector

Subscribes to a configurable set of Zenoh key-expressions, measures the
publication rate and validates payload content against declarative
expectations, and publishes a `keelson.EntityHealth` message on the
`entity_health` subject.

The active set of expectations can be replaced at runtime via the
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
  "expectations": [ ... ]
}
```

Each entry in `expectations` defines one monitored subsystem:

| Field                | Description                                                        |
|----------------------|--------------------------------------------------------------------|
| `name`               | Subsystem name emitted in `SubsystemHealth.name`                   |
| `key_expr`           | Zenoh key expression to subscribe to (supports `*` / `**`)         |
| `inactive_after_s`   | Silence longer than this → INACTIVE (default 10s)                  |
| `window_s`           | Sliding window over which the publication rate is measured (default 10s) |
| `publication_rate_hz`         | Tiered bands applied to the observed publication rate (see below)  |
| `publication_rate_default_level` | Level used when the observed rate matches no band (default CRITICAL) |
| `require_liveliness` | If `true` (default), a missing Zenoh liveliness token → UNKNOWN    |
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

### Liveliness and the UNKNOWN vs INACTIVE distinction

Keelson publishers declare a Zenoh liveliness token as long as they are
running. The connector subscribes to liveliness changes on each
expectation's `key_expr` (with `history=True`, so already-present tokens
are picked up at startup). This drives the distinction between two
failure modes:

| Liveliness | Data                                       | Level     |
|------------|--------------------------------------------|-----------|
| absent     | —                                          | UNKNOWN   |
| present    | no samples yet, or silent > `critical_after_s` | INACTIVE  |
| present    | rate / content OK                          | NOMINAL   |

Set `"require_liveliness": false` on an expectation when the data
source doesn't declare a token (e.g. MCAP replays, AIS feeds bridged
from outside the Keelson bus). The connector then falls back to
sample-based activity detection only.

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

For each subsystem, the evaluator takes the **worst** level produced by
the rate check and every content rule. The `SubsystemHealth.detail`
string lists every non-nominal contributor. The overall
`EntityHealth.level` is then the worst across all subsystems.

### Health levels

| Level         | When                                                              |
|---------------|-------------------------------------------------------------------|
| `NOMINAL`     | Liveliness present, rate in NOMINAL band, all content rules pass  |
| `DEGRADED`    | A rate or content band resolved to DEGRADED                       |
| `CRITICAL`    | A rate or content band resolved to CRITICAL                       |
| `INACTIVE`    | Alive but silent longer than `inactive_after_s`                   |
| `UNKNOWN`     | `require_liveliness` is true and no Zenoh liveliness token present |

The overall `EntityHealth.level` is the worst level among all subsystems.

## Keeping data local: monitoring sensors that don't leave the entity

A common case: a sensor (e.g. a lidar on a drone) publishes
high-bandwidth data inside the entity, but you only want the outside
world to see an aggregated health summary — not the raw stream.

This works naturally because the entity_health connector subscribes at
the **local** Zenoh peer, so the sensor's traffic only needs to be
visible inside the entity's mesh, not outside it.

### Architecture

```
┌────────────────── drone ──────────────────┐
│                                            │
│  lidar driver ─► drone/internal/.../laser_scan/lidar_front
│                          │                 │
│                          ▼                 │
│              entity_health connector       │
│                          │                 │
│                          ▼                 │
│       drone/external/.../entity_health/health
│                          │                 │
└──────────────────────────┼─────────────────┘
                           │
                egress router / bridge
                (allows only drone/external/**)
                           │
                           ▼
                    fleet operator
```

Use two namespaces (or two Keelson realms):

- `drone/internal/...` — everything produced locally; **never bridged**.
- `drone/external/...` — only what the outside world should see (the
  EntityHealth summary, possibly a low-rate position, etc.).

The "stays inside" enforcement is a **Zenoh routing concern**, not an
entity_health concern. Two common patterns:

1. **Two routers**: an internal router on the drone with no WAN
   `connect`, plus a bridge router that connects to both meshes with
   `access_control` ACLs allowing only `drone/external/**` across.
2. **One router with ACLs**: a single drone router whose
   `access_control` JSON5 config denies any non-`drone/external/**` key
   from being forwarded over its WAN endpoint.

### Connector config

The entity_health connector runs **inside the drone** and watches the
internal namespace. Run it with `--realm drone/external` so its own
output lands on the external namespace and gets bridged out:

```json
{
  "name": "lidar_front",
  "key_expr": "drone/internal/@v0/drone1/pubsub/laser_scan/lidar_front",
  "inactive_after_s": 1.0,
  "publication_rate_hz": [
    {"level": "NOMINAL",  "min": 9.0, "max": 11.0},
    {"level": "DEGRADED", "min": 5.0, "max": 15.0}
  ],
  "publication_rate_default_level": "CRITICAL",
  "require_liveliness": true
}
```

The operator only ever sees the aggregated `EntityHealth` message —
the raw `laser_scan` payloads stay inside the drone.

> **Note on lidar content checks**: today's content rules read a single
> scalar field via `getattr`. They don't aggregate over `repeated`
> fields like `LaserScan.ranges`. For pure rate + liveliness monitoring
> the example above works as-is. To check scan content (e.g. "≥100
> valid returns per scan") you'd publish a derived scalar from the
> driver, or extend the evaluator with a small aggregation primitive.

## Runtime reconfiguration

Send a Zenoh GET on the `Configurable` `set_config` RPC key with a JSON
body matching the schema above. Subscribers whose `key_expr` changed
will be re-declared; unchanged ones are kept.

## Tests

```bash
uv run pytest -vv connectors/entity_health/tests/                # all
uv run pytest -vv -m "not e2e" connectors/entity_health/tests/   # unit only
uv run pytest -vv -m e2e      connectors/entity_health/tests/    # e2e only
```
