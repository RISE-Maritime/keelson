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
  (SHA-256 of the claim-graph file), and withdrawn claims as
  `active_constraints`. No `composite_score`, no `authority_score`: this
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

## Not yet implemented

- Runtime reconfiguration via the `Configurable` RPC (`entity_health`
  has it; this connector requires a restart to change the graph).
