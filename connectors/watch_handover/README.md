# watch_handover

The vessel's answer to a ROC watch handover.

A watch handover is a briefing one operator signs over to another. When the relief
accepts, the record can be held at `pending_vessel` until the vessel itself confirms
it will accept remote operation by a new watch. This connector is that confirmation.

It forms **no opinion of its own**. `operational_authority` is already *"the vessel's
own veto on accepting remote control, computed on the vessel"*, published by whichever
Layer 3 aggregator the deployment runs (`composite_aggregator`, `warrant_aggregator`,
or another). This reads that standing verdict and compares it to a floor.

## `watch-handover2keelson`

```
usage: watch-handover2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                              [--connect CONNECT] [--listen LISTEN]
                              -r REALM -e ENTITY_ID
                              [--checklist-realm CHECKLIST_REALM]
                              [--checklist-entity CHECKLIST_ENTITY]
                              [--min-level {0,1,2,3,4,5}]
                              [--authority-max-age-s AUTHORITY_MAX_AGE_S]
```

| Option | Meaning |
|---|---|
| `-r`, `--realm` | Realm of the **vessel** — scopes the `operational_authority` this reads. |
| `-e`, `--entity-id` | The vessel this answers for. Matched against the record's `vessel.entityId`. |
| `--checklist-realm` | Realm the **checklist tree** lives under. Default `rise`. Not the same as `--realm`; see below. |
| `--checklist-entity` | Entity the checklist tree lives under — the operations centre. Default `roc1`. Not this vessel, and not the operator's station; see below. |
| `--min-level` | Lowest authority level that confirms. Default `2` (`SUPERVISED_REMOTE`). **Inert below 3** — see below. |
| `--authority-max-age-s` | Refuse rather than trust a reading older than this. Default `30`; `0` disables. |

```bash
uv run connectors/watch_handover/bin/watch-handover2keelson.py \
  --realm rise --entity-id sf18 \
  --mode client --connect tcp/127.0.0.1:7447
```

## Keys

| Direction | Key |
|---|---|
| reads | `{realm}/@v0/{entity_id}/pubsub/operational_authority/**` |
| reads + writes | `{checklist_realm}/@v0/{checklist_entity}/pubsub/checklist_handover/*` |
| declares | `{checklist_realm}/@v0/{checklist_entity}/*/watch_handover/{entity_id}` (liveliness) |

**Two things about the handover key are unusual, and both are deliberate.**

It is the ROC's own realm and entity — `rise/@v0/roc1` by default — because a handover is
a shared document several sites work on, not telemetry from one platform. That is outside
this vessel's own namespace, so the subscription is explicit rather than derived from
`--realm`. Zenoh keys are strings; a connector may subscribe to anything it is told about.

The entity names **the tree, not the station**. The checklist is one shared document —
every station writes the same key for a given run, which is what makes it shared — so two
sites pointed at two entities give one run two divergent copies. Which site an operator
actually sits at is already carried in the source id of every checklist key
(`checklist_presence/{roc_site}/{operator}`).

It was `crowsnest/@v0/checklist` until 2026-08-26. `crowsnest` is an application, not a
deployment, and using it as a realm put the same vessel in two realms at once. Whatever it
is set to here must match the ROC clients and the router's `storage_manager` key
expressions, or a confirmed handover is published where nobody is looking.

And the payload is **raw JSON, not a keelson Envelope**, because `checklist_handover`
is a PROVISIONAL subject pending [keelson#218](https://github.com/RISE-Maritime/keelson/issues/218).
An unknown subject token is precisely what makes a consumer's decode fall through to
JSON instead of failing to unwrap an envelope. When #218 lands and the subject becomes
keelson-native, both of these go away and this connector should follow.

## The rule

Two MUSTs from `OperationalAuthority.proto` are load-bearing:

- **Read `level`, never `composite_score`.** A vessel can be 92% healthy and still have
  authority `0.0`, because one essential component is down and the ceiling is
  non-compensatory. Reading the aggregate would confirm a handover the vessel is vetoing.
- **`AUTHORITY_LEVEL_UNKNOWN` is non-authorizing**, exactly like `MINIMAL_SAFE_MODE`, and
  must not be read as "no constraints known". It means the monitor could not tell.

Silence is likewise not a yes: no `operational_authority` on the wire at all is refused,
with that stated as the reason.

| Vessel says | Result | `gate` |
|---|---|---|
| nothing | refused — "the vessel cannot say ... which is not a yes" | `no_authority` |
| nothing *recently* | refused — the reading aged past `--authority-max-age-s` | `stale_authority` |
| `UNKNOWN` / `MINIMAL_SAFE_MODE` | refused — not accepting remote operation at all | `non_authorizing` |
| below `--min-level` | refused, naming the floor | `below_floor` |
| at or above `--min-level` | confirmed | `confirmed` |

Either way the verdict travels on the record — the level, the floor it was judged against,
which gate fired, the `active_constraints` that capped it, and the policy that produced it.
A confirmation that records *why* the vessel was willing is as much of an audit trail as a
refusal.

**Count `gate`, never `reason`.** `OperationalAuthority.proto` forbids parsing its own
`reason` prose and the same holds here; `gate` is a stable token so refusals can be counted
and compared without a regex over English.

### Choosing `--min-level`

**Settings 0, 1 and 2 are indistinguishable.** `UNKNOWN` and `MINIMAL_SAFE_MODE` are
non-authorizing whatever the floor says, and every level above them clears a floor of 2 or
less — so at any of those three settings the outcome is identical for every possible level.
The floor only starts deciding anything at **3**, where it begins refusing
`SUPERVISED_REMOTE`.

That matters when reading a deployment's config: `--min-level 2` is not "a low bar", it is
**no bar** — every refusal it produces is the protocol-mandated one. **This is why the
default is still 2 and no real platform has yet been given a higher floor:** a floor of 3
is the first setting that expresses a policy, and picking it is a deployment decision
nobody has taken, not an oversight. If you want the vessel
to veto degraded-but-working states, you have to say 3 or more, and then decide what to do
about the cost: refusing strands the *outgoing* operator on a degraded vessel, and a
degraded vessel needs a watch more than a healthy one.

### Staleness

The last reading is cached, so `--authority-max-age-s` is what stops a dead aggregator from
confirming handovers forever against a frozen value. Without it the module's "silence is not
a yes" rule holds only *before* the first message; after one, silence becomes an implicit
yes. Age is measured from local receipt on a monotonic clock — the failure being caught is
the publisher stopping, and that cannot be confused by vessel clock skew.

## Behaviour

**Idempotent by construction.** It acts only on records in `pending_vessel`, so its own
write echoing back is ignored, and a record any station has already driven terminal is
left alone. It also remembers what it has answered in this process, because the router
replays the key on reconnect and answering twice would stamp a second, later
`vesselConfirmedAt` over the real one.

**The consumer side is optional.** Crowsnest only sends a handover to `pending_vessel`
when its "Require the vessel to confirm" setting is on — off by default, precisely so a
fleet with no connector running does not have every handover wait until it expires.
Turn it on where this is deployed.

## Tests

```bash
uv run pytest connectors/watch_handover/tests/ -q
```
