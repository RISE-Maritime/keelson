# warrant_aggregator connector

A second Layer 3 aggregator beside `entity_health`'s compensatory
composite: instead of a score, it maintains a configured graph of claims,
each carrying the justification for relying on it, and derives the
authority level from the set of claims that remain licensed.
Non-compensatory by construction: a required ground that fails withdraws
its dependents, and healthy components elsewhere cannot offset it. Two
policies, one wire, disagreeing legibly — see the discussion in
[#199](https://github.com/RISE-Maritime/keelson/issues/199).

## What it publishes

Under its own `source_id`:

- **`operational_authority`** (`keelson.OperationalAuthority`): the
  determination. `level`, `reason`, `policy_id`, `policy_config_digest`
  (SHA-256 of the claim graph's canonical JSON, see below), and withdrawn
  claims as `active_constraints`. No `composite_score`, no `authority_score`: this
  policy derives the level from claim standings, not from a score.
- **`warrant_record`** (`keelson.WarrantRecord`): the record. Standing
  transitions as they happen and a full snapshot every
  `snapshot_period_s`, so the stream is self-contained and a recording
  reconstructs without side files. The subject is producer-neutral; any
  evaluator maintaining justification for its conclusions may publish it.

It consumes `entity_health` output; the per-subject levels in
`EntityHealth` are the evidence its rebuttal conditions check.

## Usage

```bash
warrant_aggregator2keelson \
  --realm test-realm \
  --entity-id test-vessel \
  --source-id warrant \
  --config example-graph.yaml
```

Reconstruction, from a recording made with `keelson2mcap` (or from the
`--records-jsonl` debug log):

```bash
warrant_reconstruct.py --mcap session.mcap --source-id warrant --at 300
```

## The claim graph

See [`example-graph.yaml`](example-graph.yaml). Claims either carry
`rebuttals` (source claims, bound to `(source, subject)` health levels)
or `grounds` (derived claims: `edges` with a required standing, and/or a
`redundancy` group with `min_licensed`). Standings are
`LICENSED > REDUCED > WITHDRAWN`; every claim starts `WITHDRAWN`.

Semantics: a fired rebuttal withdraws its claim; a required ground below
its requirement withdraws the dependent; grounds met but not all at full
strength reduce it; absent or stale evidence (`evidence_max_age_s`)
counts against a claim, never for it. Downgrades are immediate; upgrades
hold for `requalification_hold_s`, applied where evidence acts directly
(source claims and the ladder level) while derived standings follow their
grounds immediately. The level is the highest `autonomy_ladder` rung
whose requirements hold; rung names must be `AuthorityLevel` names.

## Runtime reconfiguration

The connector serves the `configurable/v1` RPC interface under its own
`source_id`:

```text
{realm}/@v0/{entity_id}/@rpc/configurable/v1/get_config/{source_id}
{realm}/@v0/{entity_id}/@rpc/configurable/v1/set_config/{source_id}
```

**Off by default.** A deployment is locked unless the connector is started
with `--runtime-reconfiguration`: `get_config` still answers, but `set_config`
is refused with a reply error and nothing changes, so the graph that runs is
the one on disk at startup for the life of the process. That is the mode for
an operational deployment where the graph is a certified artefact. Enable
reconfiguration deliberately, for development, SIL/HIL, integration,
commissioning and trials, where the graph is being tuned while watching what
it does. The switch is a command-line flag and not a key in the graph
document on purpose: a document that could unlock itself would not be a
lock.

`get_config` replies with the claim graph as loaded, as JSON. When enabled,
`set_config` takes the same document as JSON (JSON is YAML, so it is exactly the file's
mapping) and replaces the running graph. A document that fails validation —
the rules are the ones `model.py` enforces on a file — is refused with the
`ValueError` text as the reply error, and nothing changes.

An accepted document restarts the argument: **every claim is WITHDRAWN again
and the level falls to the floor rung**, then re-licenses after
`requalification_hold_s` exactly as at startup. The evidence already seen is
kept, so the first evaluation does not fire every rebuttal on absence; the
conclusions are not, because a standing nobody evaluated under the new rules
is not one this connector will publish. The snapshot that follows is the
first record under the new digest, and every applied configuration is also
republished on `configuration_json`.

### The digest

`policy_config_digest` is the SHA-256 of the graph's **canonical JSON**:
keys sorted at every level, no whitespace, UTF-8, integral floats written as
integers (`5.0` → `5`). It identifies the policy rather than the bytes of
one file, so a YAML file, the same document delivered over `set_config`, and
a client's draft of the same graph all carry the same digest — which is what
lets a consumer confirm the vessel runs the document it holds. The digest is
identity and traceability only — it says which policy produced a
determination; it is not an authorisation to run that policy. Before
reconfiguration existed the digest was taken over the file bytes; a
recording made then carries a digest the file no longer reproduces.
