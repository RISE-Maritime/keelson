# Health Monitoring

Keelson's layered health monitoring provides generic building blocks — presence detection, health scoring, and composite aggregation — that any application-specific decision layer can consume.

> **Phase 1 status:** This document covers the protocol conventions, message definitions, and reference configuration schema. The aggregator implementation is planned for Phase 2.

## Overview

Health monitoring in keelson follows a 3-layer architecture:

| Layer | Responsibility | Mechanism |
|-------|---------------|-----------|
| **Layer 1 — Presence** | Detect whether source processes are running | Zenoh liveliness tokens |
| **Layer 2 — Health assessment** | Evaluate per-component health; produce a composite score | Health aggregator (configurable) |
| **Layer 3 — Application logic** | Consume the composite score to drive domain-specific decisions | Application-defined (see examples below) |

Layers 1–2 are generic keelson infrastructure. Layer 3 is where applications map the composite score to actionable decisions.

## Layer 1: Liveliness (Presence Detection)

Each source process declares a liveliness token using the convention defined in the [protocol specification, Section 5](protocol-specification.md#5-liveliness-key-space-convention):

```
{base_path}/@v0/{entity_id}/pubsub/*/{source_id}
```

The `*` wildcard in the subject position signals that the source is alive and may produce output on any subject. This is a coarse presence signal — the token does not declare which specific subjects the source publishes.

A health aggregator subscribes to liveliness events to detect source join/leave:

```python
session.liveliness().declare_subscriber(
    "keelson/@v0/landkrabban/pubsub/**",
    callback,
)
```

See [protocol specification, Section 5](protocol-specification.md#5-liveliness-key-space-convention) for full details on token format, subscriber patterns, and verbatim chunk isolation.

### Declaring liveliness in connectors

Any connector that publishes data into keelson (a source/ingestion connector) should declare a liveliness token. The token signals "this source process is alive and may produce output."

**When to declare:** Source connectors that publish to `pubsub/` key expressions, such as `ais2keelson`, `n2k2keelson`, `nmea01832keelson`, or `platform-geometry2keelson`.

**When NOT to declare:**
- Sink connectors (subscribers/recorders like `keelson2foxglove`, `keelson2mcap`) — they have no `--source-id` and don't publish into keelson
- Offline utilities (`klog2mcap`, `mcap-tagg`) — not long-running network processes
- RPC-only services (`mediamtx-whep`) — until a separate RPC liveliness convention is defined

**Pattern:** Use the `declare_liveliness_token` context manager from `keelson.scaffolding` immediately after opening the Zenoh session. The token is automatically undeclared when the `with` block exits:

```python
from keelson.scaffolding import declare_liveliness_token

with zenoh.open(conf) as session:
    with declare_liveliness_token(session, args.realm, args.entity_id, args.source_id):
        run(session, args)
```

**What it gives you:** Health aggregators and monitoring UIs can detect source join/leave events without polling. When a connector process starts, it appears in the liveliness set; when it exits (cleanly or via crash), the token is automatically removed and subscribers receive a leave event.

## Layer 2: Health Aggregation

The health aggregator is a generic, configurable component that produces a single composite score (0.0–1.0) for downstream consumers. It evaluates per-component health using a weighted scoring model. Each component is assigned:

- **weight** — its relative importance in the composite score (all weights should sum to 1.0)
- **stale_threshold_ms** — maximum age of the last received message before the component is considered stale (health score → 0.0)
- **health_rules** — conditions evaluated against incoming messages

### Health rules

Each rule inspects a specific subject and evaluates a condition:

| Rule type | Description | Example |
|-----------|-------------|---------|
| Value threshold | Numeric comparison against a message field | `good_if: "value < 2.0"` |
| Enum/state requirement | Exact match against an expected value | `require: "FIX_3D"` |
| Message rate | Frequency of messages on a subject | `good_if: "> 20 Hz"` |

A component's health score is determined by the worst-performing rule:

- All rules pass `good_if` → score = 1.0
- At least one rule in `degraded_if` range → score = 0.5
- Any rule fails all conditions or the component is stale → score = 0.0

### Composite score

The composite score is the weighted sum of all component scores:

```
composite_score = Σ (component_weight × component_score)
```

This normalized score is the output of Layer 2. Layer 3 consumers interpret it according to their own domain logic.

## Layer 3: Application-Specific Decision Logic

The composite score produced by Layer 2 is the input to whatever domain-specific logic a deployment requires. Applications subscribe to the composite score and apply their own rules to translate it into actionable decisions. Keelson does not prescribe what those decisions are — it only guarantees a well-defined, normalized health signal.

Example use cases:

- **Operational authority for autonomous vessels** — map the composite score to authority levels (detailed below)
- **Dashboard health indicators** — translate the score into green / yellow / red status for operator UIs
- **Automated alerting or degraded-mode switching** — trigger alarms or fall back to a safe mode when the score drops below a threshold

### Example: Operational Authority for Autonomous Vessels

This built-in example maps the composite score to operational authority levels aligned with the IMO MASS (Maritime Autonomous Surface Ships) framework.

The aggregator publishes an [`OperationalAuthority`](subjects-and-types.md) message to:

```
{base_path}/@v0/{entity_id}/pubsub/operational_authority/{aggregator_id}
```

#### Message format

The message contains:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `google.protobuf.Timestamp` | Time of the authority determination |
| `level` | `AuthorityLevel` enum | Current authority level |
| `composite_score` | `float` | Normalized composite health score (0.0–1.0) |
| `reason` | `string` | Human-readable explanation |
| `component_scores` | `map<string, float>` | Per-component health scores for observability |

#### Authority levels

The `AuthorityLevel` enum is aligned with the IMO MASS framework:

| Value | Name | Description |
|-------|------|-------------|
| 0 | `AUTHORITY_LEVEL_UNKNOWN` | Authority level has not been determined |
| 1 | `AUTHORITY_LEVEL_MINIMAL_SAFE_MODE` | Minimal safe operation (e.g., all-stop, hold position) |
| 2 | `AUTHORITY_LEVEL_SUPERVISED_REMOTE` | Remote operator with limited situational awareness |
| 3 | `AUTHORITY_LEVEL_REMOTE_CONTROLLED` | Full remote control with good situational awareness |
| 4 | `AUTHORITY_LEVEL_ASSISTED_AUTONOMOUS` | Autonomous with operator supervision |
| 5 | `AUTHORITY_LEVEL_FULL_AUTONOMOUS` | Fully autonomous operation |

#### Authority thresholds and hysteresis

The composite score is mapped to an authority level using configurable thresholds. The aggregator selects the highest authority level whose threshold is met:

| Authority level | Default threshold |
|----------------|-------------------|
| `FULL_AUTONOMOUS` | ≥ 0.85 |
| `ASSISTED_AUTONOMOUS` | ≥ 0.65 |
| `REMOTE_CONTROLLED` | ≥ 0.45 |
| `SUPERVISED_REMOTE` | ≥ 0.25 |
| `MINIMAL_SAFE_MODE` | < 0.25 |

A **hysteresis band** (default: 0.05) prevents rapid oscillation between levels. Transitioning down requires the score to drop below `threshold - hysteresis`, and transitioning up requires the score to exceed `threshold + hysteresis`.
