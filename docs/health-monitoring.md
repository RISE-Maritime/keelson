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

Liveliness follows the three-tier convention defined in the [protocol specification, Section 5](protocol-specification.md#5-liveliness-key-space-convention). A producing process declares:

1. **One source-level token** — "this process is present as a producer":

   ```
   {base_path}/@v0/{entity_id}/*/{source_id}
   ```

2. **One subject-level token per subject it is configured to publish** — capability, not activity; never retracted on data silence:

   ```
   {base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}
   ```

3. **One interface-level token per served RPC `(interface, version)`**:

   ```
   {base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/*/{source_id}
   ```

A health aggregator subscribes to liveliness events to detect join/leave and to learn each source's advertised publishing surface:

```python
# Subject-level + legacy coarse tokens for one watched source:
session.liveliness().declare_subscriber(
    "keelson/@v0/landkrabban/pubsub/*/gnss/0", callback, history=True,
)
# Source-level tokens for the same source:
session.liveliness().declare_subscriber(
    "keelson/@v0/landkrabban/*/gnss/0", callback, history=True,
)
```

Received sample keys are classified by their literal chunks (`*` in the category slot → source-level; `pubsub` + `*` subject → legacy coarse; concrete subject → subject-level) — see [protocol specification, Section 5.5](protocol-specification.md#55-discovery-query-patterns), including why `@rpc`-tier tokens need their own subscription.

See the protocol specification for full details on token formats, discovery patterns, the producer/consumer asymmetry, and verbatim chunk isolation.

### Declaring liveliness in connectors

A connector with a **producing role** (publishes pubsub data and/or serves RPC) declares the source-level token plus its per-capability tokens. A **pure consumer** declares nothing.

**When to declare:** Source connectors that publish to `pubsub/` key expressions (such as `ais2keelson`, `n2k2keelson`, `nmea01832keelson`, `platform-geometry2keelson`) and RPC servers (such as `mediamtx-whep`, which declares source-level + `whep_proxy/v1` interface tokens but no subject tokens).

**When NOT to declare:**
- Sink connectors (subscribers/recorders like `keelson2foxglove`, `keelson2mcap`) — pure consumers; their visibility is a system-level concern (systemd/container health checks, log shipping, output artifact inspection), not a wire concern
- Offline utilities (`klog2mcap`, `mcap-tagg`) — not long-running network processes

**Pattern:** Use the `declare_liveliness` composite from `keelson.scaffolding` immediately after opening the Zenoh session — it declares the source-level token plus one token per listed subject (and RPC interface tokens for servers not built on `serve_rpc`, which declares its own). Everything is undeclared when the `with` block exits:

```python
from keelson.scaffolding import declare_liveliness

with zenoh.open(conf) as session:
    with declare_liveliness(
        session, args.realm, args.entity_id, args.source_id,
        pubsub_subjects=SUPPORTED_SUBJECTS,
    ):
        run(session, args)
```

Dynamic publishing surfaces (device enumeration, config reload) use `PubsubSubjectLivelinessManager` to add/remove subject tokens at runtime, alongside a `declare_source_liveliness` held for the process lifetime.

**What it gives you:** aggregators and monitoring UIs can detect source join/leave without polling *and* see exactly which subjects each source claims to publish. The `entity_health` connector uses this to distinguish "source down" (`UNKNOWN`), "source up but doesn't advertise this subject" (`NOT_ADVERTISED` — typically a config typo), and "advertised but silent" (`INACTIVE`).

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

The composite score is the unweighted mean over all participating sources of
each source's *effective* score:

```
effective_score  = health_score × coverage_fraction
composite_score  = mean(effective_score over participating sources)
```

`health_score` is the source's rolled-up health level mapped to a score
(NOMINAL 1.0, DEGRADED 0.5, everything else 0.0). `coverage_fraction` is the
share of the source's watched subjects that reached a determinate verdict —
only UNKNOWN reduces it (a known failure is evidence, not missing evidence),
and NOT_ADVERTISED subjects leave the denominator entirely, since a watch
pointed at a subject the source never claims is a fact about the monitor's
config, not the vessel. Sources whose roll-up is itself NOT_ADVERTISED do not
participate in the mean at all.

This normalized score is the output of Layer 2. Layer 3 consumers interpret it
according to their own domain logic.

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
| `level` | `AuthorityLevel` enum | Current authority level — derived from `authority_score` through a sticky (hysteresis) ladder |
| `composite_score` | `float` | Normalized composite health score (0.0–1.0). **Deliberately uncapped**: how healthy the monitored fleet is, in aggregate |
| `authority_score` | `optional float` | `min(composite_score, every active essential ceiling)` — what the vessel is actually permitted to claim. Unset when no valid determination exists (which is distinct from an ordinary all-stop of 0.0) |
| `reason` | `string` | Human-readable explanation only — consumers must act on `level`/`authority_score`, never parse this |
| `component_scores` | `map<string, float>` | Per-source effective scores for observability |
| `source_assessments` | `repeated SourceAssessment` | Per-source arithmetic left visible: `health_score`, `coverage_fraction`, `effective_score`, unassessed/not-advertised subjects, `essential` flag |
| `active_constraints` | `repeated AuthorityConstraint` | Every essential ceiling currently limiting authority, with its cause |
| `policy_id`, `policy_config_digest` | `optional` | Identify the policy/config that produced the determination, for replay and audit |

The two scores are allowed to disagree, and that disagreement is the design: a
fleet with one dead essential component and eleven healthy sources reports
`composite_score ≈ 0.92` (true — the fleet *is* mostly healthy) alongside
`authority_score = 0.0` and `MINIMAL_SAFE_MODE` (also true — the vessel may
not be relied on). Components marked `essential` in the watch config impose
non-compensatory ceilings: no amount of healthy unrelated equipment buys a
failed prerequisite back.

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
