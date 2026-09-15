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
                              [--connect CONNECT] [--listen LISTEN] [--zenoh-config ZENOH_CONFIG]
                              -r REALM -e ENTITY_ID [--checklist-realm CHECKLIST_REALM]
                              [--checklist-entity CHECKLIST_ENTITY] [--min-level {0,1,2,3,4,5}]
                              [--authority-max-age-s AUTHORITY_MAX_AGE_S]
                              [--authority-source-id AUTHORITY_SOURCE_ID]
                              [--startup-grace-s STARTUP_GRACE_S]
                              [--answer-interval-s ANSWER_INTERVAL_S]
                              [--answer-max-attempts ANSWER_MAX_ATTEMPTS]

Confirm or refuse a ROC watch handover from this vessel's operational_authority.

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO)
  --mode, -m {peer,client}
                        The Zenoh session mode.
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447
  --zenoh-config ZENOH_CONFIG
                        Path to a Zenoh configuration file (JSON5). Everything the flags above
                        cannot express — access control, QoS defaults, transport tuning — lives
                        here. --mode/--connect/--listen still win where they overlap. Falls back
                        to the ZENOH_CONFIG environment variable.
  -r, --realm REALM
  -e, --entity-id ENTITY_ID
  --checklist-realm CHECKLIST_REALM
                        Realm the checklist tree lives under. The handover key is NOT under
                        --realm; see the module docstring.
  --checklist-entity CHECKLIST_ENTITY
                        Entity the checklist tree lives under — the operations centre, not this
                        vessel and not the operator's station. Must match what the ROC clients and
                        the router's storage are configured with, or the handover is published
                        where nobody is looking.
  --min-level {0,1,2,3,4,5}
                        Lowest authority level that confirms a handover. 0=UNKNOWN,
                        1=MINIMAL_SAFE_MODE, 2=SUPERVISED_REMOTE, 3=REMOTE_CONTROLLED,
                        4=ASSISTED_AUTONOMOUS, 5=FULL_AUTONOMOUS. NOTE: [0, 1] are non-authorizing
                        whatever this is set to, so 0, 1 and 2 all behave identically — the floor
                        only decides anything at 3 or above, which is why 3 is the default.
                        Setting 2 or less is not a lower bar, it is NO bar: every refusal it can
                        produce is the protocol-mandated one.
  --authority-max-age-s AUTHORITY_MAX_AGE_S
                        Refuse rather than trust an operational_authority reading older than this.
                        Guards against the aggregator dying while the vessel reads a high level,
                        which would otherwise confirm handovers forever against a frozen value.
                        Default 30s; 0 disables the check.
  --authority-source-id AUTHORITY_SOURCE_ID
                        Read operational_authority from this source only. By default every source
                        under the entity is read and the LOWEST level among fresh readings
                        governs, since any aggregator reporting a constraint is the vessel being
                        constrained. Pin a source when a deployment wants one aggregator to be
                        authoritative.
  --startup-grace-s STARTUP_GRACE_S
                        Wait this long for a first operational_authority before refusing a
                        handover for the lack of one. The router replays a retained handover
                        immediately while the aggregator's next sample is up to a publish period
                        away, so without this a restart mid-handover refuses a healthy vessel —
                        terminally. Default 12s; only this one gate waits.
  --answer-interval-s ANSWER_INTERVAL_S
                        How often to re-examine pending handovers. Default 2s.
  --answer-max-attempts ANSWER_MAX_ATTEMPTS
                        Publish an answer at most this many times. The put is fire-and-forget
                        under DROP, so an answer that is shed leaves the record pending with
                        nobody returning to it; the record leaving pending_vessel is the only
                        acknowledgement available. Default 5.
```

| Option | Meaning |
|---|---|
| `-r`, `--realm` | Realm of the **vessel** — scopes the `operational_authority` this reads. |
| `-e`, `--entity-id` | The vessel this answers for. Matched against the record's `vessel.entityId`. |
| `--checklist-realm` | Realm the **checklist tree** lives under. Default `rise`. Not the same as `--realm`; see below. |
| `--checklist-entity` | Entity the checklist tree lives under — the operations centre. Default `roc1`. Not this vessel, and not the operator's station; see below. |
| `--min-level` | Lowest authority level that confirms. Default `3` (`REMOTE_CONTROLLED`). **Inert below 3** — see below. |
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
**no bar** — every refusal it can produce is the protocol-mandated one, and the flag is
never actually consulted.

**The default is 3.** A gate that reads as a safety control in the config file and cannot
refuse anything is worse than no gate, because it is trusted. At 3 the connector refuses
`SUPERVISED_REMOTE` — a vessel that is available for remote work but degraded — which is
the first thing it can say that the protocol was not already saying for it.

The cost is real and worth stating plainly: **a refusal strands the outgoing operator**, on
a watch they were trying to hand over, at exactly the moment the vessel most needs one. A
degraded vessel needs a watch more than a healthy vessel does. That is an argument for
having a documented escalation path when `below_floor` fires — not an argument for a floor
that cannot fire.

A deployment that wants the old behaviour sets `--min-level 2`. The difference is that the
2 is now a decision somebody took, and it is visible in the record: `below_floor` never
appears in a deployment running at 2 or below.

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
