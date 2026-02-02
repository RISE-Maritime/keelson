# Health Monitoring

This document describes the keelson health monitoring architecture for adaptive autonomy, aligned with the IMO MASS (Maritime Autonomous Surface Ships) framework.

> **Phase 1 status:** This document covers the protocol conventions, message definitions, and reference configuration schema. The aggregator implementation is planned for Phase 2.

## Overview

Health monitoring in keelson follows a 3-layer architecture:

| Layer | Responsibility | Mechanism |
|-------|---------------|-----------|
| **Layer 1 — Presence** | Detect whether source processes are running | Zenoh liveliness tokens |
| **Layer 2 — Health assessment** | Evaluate per-component health from data quality, message rates, and staleness | Health aggregator (configurable) |
| **Layer 3 — Authority determination** | Compute a composite score and map it to an operational authority level | `OperationalAuthority` message |

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

## Layer 2: Health Aggregator Configuration

The health aggregator evaluates per-component health using a weighted scoring model. Each component is assigned:

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

### Reference configuration

A complete reference configuration is available at [`health-aggregator-config-schema.yaml`](health-aggregator-config-schema.yaml). It demonstrates the structure for a typical autonomous vessel with GNSS, camera, IMU, radar, and network components.

## Layer 3: OperationalAuthority Message

The aggregator publishes an [`OperationalAuthority`](subjects-and-types.md) message to:

```
{base_path}/@v0/{entity_id}/pubsub/operational_authority/{aggregator_id}
```

The message contains:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `google.protobuf.Timestamp` | Time of the authority determination |
| `level` | `AuthorityLevel` enum | Current authority level |
| `composite_score` | `float` | Normalized composite health score (0.0–1.0) |
| `reason` | `string` | Human-readable explanation |
| `component_scores` | `map<string, float>` | Per-component health scores for observability |

### Authority levels

The `AuthorityLevel` enum is aligned with the IMO MASS framework:

| Value | Name | Description |
|-------|------|-------------|
| 0 | `AUTHORITY_LEVEL_UNKNOWN` | Authority level has not been determined |
| 1 | `AUTHORITY_LEVEL_MINIMAL_SAFE_MODE` | Minimal safe operation (e.g., all-stop, hold position) |
| 2 | `AUTHORITY_LEVEL_SUPERVISED_REMOTE` | Remote operator with limited situational awareness |
| 3 | `AUTHORITY_LEVEL_REMOTE_CONTROLLED` | Full remote control with good situational awareness |
| 4 | `AUTHORITY_LEVEL_ASSISTED_AUTONOMOUS` | Autonomous with operator supervision |
| 5 | `AUTHORITY_LEVEL_FULL_AUTONOMOUS` | Fully autonomous operation |

### Authority thresholds and hysteresis

The composite score is mapped to an authority level using configurable thresholds. The aggregator selects the highest authority level whose threshold is met:

| Authority level | Default threshold |
|----------------|-------------------|
| `FULL_AUTONOMOUS` | ≥ 0.85 |
| `ASSISTED_AUTONOMOUS` | ≥ 0.65 |
| `REMOTE_CONTROLLED` | ≥ 0.45 |
| `SUPERVISED_REMOTE` | ≥ 0.25 |
| `MINIMAL_SAFE_MODE` | < 0.25 |

A **hysteresis band** (default: 0.05) prevents rapid oscillation between levels. Transitioning down requires the score to drop below `threshold - hysteresis`, and transitioning up requires the score to exceed `threshold + hysteresis`.
