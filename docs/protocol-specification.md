# Protocol specification

In short, keelson has opinions about:

* The format of the key used when publishing data to zenoh
* The format of the data published to zenoh
* The format of the key used when declaring a queryable (i.e. RPC endpoint) in zenoh
* The format of the requests and responses exchanged via a queryable (i.e. RPC endpoint) in zenoh

## 1. Common key-space design

In zenoh, both pub/sub and req/rep (queryables) messaging patterns all live in the same shared key "space". In keelson, the shared key-space has a common base hierarchy of three (3) levels:

`{base_path}/@v{major_version}/{entity_id}/...`

With:

* `base_path` being any base_path where to operate
* `@v{major_version}` is the major version of keelson used, the leading `@` makes this a verbatim chunk, allowing separation of different major versions.
* `entity_id` being a unique id representing an entity within the realm (Normally the platform name ei. landkrabban, masslab, logging_pc_one)
* `...` are specific key levels depending on the messaging pattern, these are further described below.

> **NOTE:** Without exceptions, keys should adhere to `snake_case` style.

> **NOTE:** [Verbatim chunks](https://zenoh.io/blog/2024-04-30-zenoh-electrode/) allows some key spaces to be hermetically sealed from each other. Any chunk that starts with `@` is treated as a verbatim chunk, and can only be matched by an identical chunk. In general, verbatim chunks are useful in ensuring that `*` and `**` accidentally match chunks that are not supposed to be matched. A common case is API versioning where `@v1` and `@v2` should not be mixed or at least explicitly selected.


### Publish, Subscribe & RPC (Queryable)

RPC stands for remote procedure call and refers to the queryables in zenoh. So both connectors and processors can use both pubsub and rpc (queryables) depending on how the api is designed.

## 2. PUBSUB - Publish- Subscribe messaging

### 2.1 Specific key-space design

For pub/sub messaging, the lower levels of the key-space has the following levels:

  `.../pubsub/{subject}/{source_id}`

With

* `pubsub` being the hard-coded word "pubsub" letting users directly identify key expression category  
* `subject` being a well-known subject describing the information contained within the payloads published to this key. The concept of subjects is further described under Data format below.
* `source_id` being a unique id for the source producing the information described by `subject`. `source_id` may contain any number of addititional levels (i.e. forward slashes `/`) ei. camera/rbg/0

#### 2.1.1 Target Extension

When a source produces data about external entities (rather than the entity running the source itself), the key can include an optional `@target` extension:

  `.../pubsub/{subject}/{source_id}/@target/{target_id}`

With:

* `@target` being the hard-coded word "@target" indicating this data refers to an external entity. The `@` makes this a verbatim chunk.
* `target_id` being a unique identifier for the referred entity (e.g., `mmsi_245060000` for an AIS-tracked vessel).

**Example:** An AIS receiver on entity `shore_station` publishing heading data about vessel with MMSI 245060000:

```
keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000
```

##### Verbatim chunk isolation

The `@target` prefix is a verbatim chunk, meaning it is **hermetically isolated** from wildcards. This is an intentional design decision:

* A subscriber to `.../pubsub/{subject}/{source_id}` will NOT receive messages with `@target` extensions
* A subscriber to `.../pubsub/{subject}/{source_id}/**` will NOT receive messages with `@target` extensions (wildcards cannot cross verbatim boundaries)
* To receive targeted messages, subscribers MUST explicitly include `@target` in their patterns

**Subscription pattern examples** for key `.../pubsub/location_fix/ais/@target/mmsi_123456`:

| Pattern | Matches? | Reason |
|---------|----------|--------|
| `.../ais` | No | Different key length |
| `.../ais/**` | No | `**` cannot cross verbatim `@target` |
| `.../ais/*` | No | `*` cannot match verbatim `@target` |
| `.../ais/@target/**` | Yes | Explicit verbatim match |
| `.../ais/@target/mmsi_*` | Yes | Verbatim @target + wildcard |

To receive both targeted and non-targeted messages from a source, subscribers need multiple patterns:
* `.../pubsub/{subject}/{source_id}` — non-targeted messages
* `.../pubsub/{subject}/{source_id}/@target/**` — all targeted messages

##### When to use @target

Use the `@target` extension when:
* The source observes or tracks external entities (e.g., AIS receivers tracking other vessels)
* Data describes something other than the entity running the source
* You need to distinguish between self-observations and observations of others

Do NOT use `@target` when:
* The data describes the entity itself (e.g., own-ship position from onboard GNSS)
* The source_id sufficiently identifies the data origin

##### Why `@target` is verbatim (and why no other extensions should be)

The verbatim isolation is load-bearing for one specific property: a subscriber
to `pubsub/{subject}/**` receives **only** self-observations, with no risk of
silently mixing in observations of external entities published on the same
subject. This matters for safety-critical consumers such as own-ship state
estimators, which must not fuse AIS-tracked-vessel positions as if they were
own-ship sensor readings.

The cost of that isolation is **discoverability**: any "capture everything"
consumer (recorders, replay tools, audit pipelines) cannot rely on a single
`pubsub/**` subscription — it must explicitly include `pubsub/**/@target/**`
alongside, per the dual-pattern idiom above. The mcap and klog connector
examples in this repository demonstrate this idiom.

This trade-off — wildcard isolation at the cost of discoverability — should
**not** be replicated for other classes of context. The discoverability tax
compounds with each new verbatim extension, and most context distinctions
(producer role, setpoint vs. measurement, modality of observation, …) can be
expressed via `source_id` without introducing a new verbatim chunk. A new
verbatim extension should clear two bars:

1. The isolation property prevents a safety-class bug (an accidental mixing
   would cause a class-of-failure meaningfully worse than "annoying"), and
2. The marker carries structured payload that benefits from key-pattern
   matching (`@target/{target_id}` supports `@target/mmsi_*` subscriptions —
   a property `source_id` encoding alone could not give cleanly).

`@target` clears both bars. Future candidates should be evaluated against
them rather than added by analogy.

### 2.2 Message format specification

Each message published to zenoh must be a protobuf-encoded keelson `Envelope`. An `Envelope` contains exactly one (1) `payload`, we say that a `payload` is **enclosed** within an `Envelope` by the publisher and can later be **uncovered** from that `Envelope` by the subscriber. 

[sketch](./subject_payload_schema.drawio.svg)

Keelson support a set of well-known `payload`s, defined by the protobuf schemas available in [messages](https://github.com/RISE-Maritime/keelson/messages/payloads/). Each well-known `payload` is associated with an informative `subject`, the mapping between `subject`s and `payload`s is maintained in a [look-up table in YAML format](https://github.com/RISE-Maritime/keelson/messages/subjects.yaml).

The main design principles behind this scheme are:

* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with one or more subjects that describes how to interpret the **information**.
* Each subject or procedure is part of the key when publishing data to zenoh, refer to the section about [keys](#21-specific-key-space-design), this helps the sender and receiver to put the information into a **context**.

#### 2.2.1 What belongs on the bus (and in what shape)

Before adding a subject, classify the data. There are three kinds, each with its own treatment. The naming convention in the next section then applies only once something has earned a subject at all.

**1. Observations → measurement subjects.**
A directly-observed quantity. The granularity is the key decision, and it turns on **separability** — *not* on how many fields the data has:

* **1a — separable scalar quantities → one subject each.** When the parts are independently meaningful, independently subscribable, and substitutable across sources, publish each on its own **primitive payload** subject — never glued into a bundle, even when one sensor produces them together. Wind speed and direction are separate subjects (`true_wind_speed_mps`, `true_wind_direction_deg`); so are `air_temperature_celsius` / `air_pressure_hpa`. Apply the same to precipitation, sea state, ice, etc. Correlating co-produced subjects ("what was the state at time *T*") is the **consumer's** job, by timestamp — the bus stays normalized.
  * scalar → [`keelson.TimestampedFloat`](https://github.com/RISE-Maritime/keelson/messages/payloads/Primitives.proto); boolean → `keelson.TimestampedBool`.
  * a value *over a window* (max / mean / accumulation over an interval) → `keelson.TimestampedFloatPeriod` — the window lives in the `period` field, so one subject covers every window length. The aggregation kind ("max", "accumulation") goes in the subject *name*.
  * an observed categorical → a small `{ timestamp; <Enum> }` message (e.g. `PrecipitationType`, `IceType`). Use a typed enum for closed, standardized vocabularies; `keelson.TimestampedString` for open / vendor strings.
* **1b — an indivisible structured frame → one complex-payload subject, kept whole.** When the parts are only meaningful *together* — a single source's coherent output at one instant — the frame **is** the atom and must not be decomposed. A point cloud, image, laser scan, radar sweep, or audio frame is one subject (`point_cloud`, `compressed_image`). "Publish just the x-coordinates of a lidar frame on their own subject" is meaningless; do not try.

> **The test that decides 1a vs 1b:** *would each part be independently meaningful and useful to a consumer as its own subject?* Wind speed — yes → split (1a). A point cloud's coordinates — no → keep whole (1b). This is the same line as the **primitive** vs **complex** payload distinction in the next section.

**2. Derived / classification data → not on the bus at all.**
If a value can be computed from another subject by a fixed mapping, it is a presentation concern. It gets **no subject, and no SDK helper** — keelson is not in the classification business. Publish the underlying measurement; the consuming application owns the mapping.

> Examples: Beaufort force (from wind speed), a visibility band (from range in metres), a coarse "weather category" label. All belong in the consumer/UI, not on the bus. Putting them on the bus creates a second source of truth that can silently disagree with the measurement it was derived from.

> **The test that separates a #1 observation from #2 derived data:** *can a consumer compute this from another subject via a fixed table?* **Yes** → derived → off the bus. **No** — it is irreducible (a scalar you read off an instrument, or a categorical you *observe* like rain-vs-snow or ice type, rather than calculate) → it is a real observation (#1).

**3. External products → bundled types, quarantined behind a clear boundary.**
Data *authored elsewhere* — authority alerts, model forecasts — is inherently a bundle, carries provenance (`issuer` / `source`, `issued_at`) and a validity window. This is the only place allowed to aggregate quantities that *would each be independently useful on their own*, and it is allowed only because the value is the coherent issued document. Keep the scope confined:

* carry exactly what the contract needs, no kitchen sink;
* internal sub-structures are **nested** types, so they can never acquire their own subject or leak into the live domain;
* do **not** embed live measurement types — mirror field *names* if you want zero-translation reads, but keep the types local;
* foreign labels passed through are free-form `string`, not enums keelson maintains.

**Shared vocabularies** (e.g. a single `SeverityLevel`) live in one shared `.proto`, never re-declared per domain.

The whole rule in one table:

| Data | Parts independently useful? | Foreign authored artifact? | Verdict |
|---|---|---|---|
| wind speed / direction | yes | no | split → primitive subjects (1a) |
| point cloud, image, scan | no | no | one complex subject, kept whole (1b) |
| a live "weather" aggregate | yes | no | **not a subject** — split the parts, drop the bundle |
| weather forecast / alert | yes | yes | bundled external product, quarantined (#3) |

This extends the existing guidance to *reuse existing subjects rather than re-model* (PR #154): that says don't duplicate what already exists; this says, for genuinely new data, which of the three bins it goes in.

#### 2.2.2 Naming convention for `subject`s category

There are three distinct kind of payloads that has to be covered by a naming convention for `subject`s:

* **raw** "arbitrary bytes", where we do not know the schema or do not want to express the schema as a protobuf type, these all fall under the special subject `raw` using the payload type [`TimestampedBytes`](https://github.com/RISE-Maritime/keelson/messages/payloads/TimestampedBytes.proto)
* **primitive payloads**, which have a specific meaning but where the protobuf type is generic, i.e [`TimestampedFloat`](https://github.com/RISE-Maritime/keelson/messages/payloads/TimestampedFloat.proto) or similar. In this case the subject needs to be very informative with regards to that value and we employ the following convention: `<entity>_<property>_<unit>` where `entity`, `property` and `unit` are constrained to alphanumeric characters. For example `rudder_angle_deg`.
* **complex payloads**, which have a specific protobuf type that is not shared with any other subject. In this case, the subject name should be the snake_case version of the protobuf message name, for example `RawImage` -> `raw_image`.

In general, [`subjects.yaml`](https://github.com/RISE-Maritime/keelson/messages/subjects.yaml) contains the current well-known subjects and can be regarded as the style-guide to follow.

### Units Summary in Subjects
| Unit Symbol   | Full Unit Name                  | Example Subjects Using It                                      |
|--------------|---------------------------------|----------------------------------------------------------------|
| m            | meter                           | location_fix_accuracy_horizontal_m, draught_mean_m, altitude_msl_m |
| deg          | degree (angle)                  | heading_true_north_deg, roll_deg, target_bearing_relative_deg  |
| degps        | degrees per second              | roll_rate_degps, yaw_rate_degps                                |
| knots        | nautical miles per hour         | speed_over_ground_knots, speed_through_water_knots             |
| pct          | percent                         | engine_throttle_pct, wheel_position_pct, battery_state_of_charge_pct |
| rpm          | revolutions per minute          | propeller_rate_rpm, engine_rate_rpm                            |
| celsius      | degrees Celsius                 | engine_oil_temperature_celsius, air_temperature_celsius        |
| psi          | pounds per square inch          | engine_oil_pressure_psi, engine_coolant_pressure_psi           |
| lph          | liters per hour                 | engine_fuel_rate_lph                                           |
| l            | liters                          | engine_fuel_consumed_l                                         |
| volt         | volts                           | battery_voltage_volt, battery_min_voltage_volt                 |
| amp          | amperes                         | battery_current_amp                                            |
| amph         | ampere-hours                    | battery_capacity_amph                                          |
| ah           | ampere-hours                    | battery_current_consumed_ah                                    |
| wh           | watt-hours                      | battery_energy_consumed_wh                                     |
| sec          | seconds                         | battery_time_remaining_sec, device_uptime_duration             |
| hpa          | hectopascal                     | air_pressure_hpa                                               |
| ppt          | parts per thousand               | water_salinity_ppt                                             |
| mps          | meters per second                | true_wind_speed_mps, climb_rate_mps, surge_velocity_mps        |
| mpss         | meters per second squared        | linear_acceleration_mpss, surge_acceleration_mpss              |
| radps        | radians per second               | angular_velocity_radps                                         |
| gauss        | gauss (magnetic field strength)  | magnetic_field_gauss                                           |
| s            | seconds                          | heave_period_s, target_tcpa_s                                  |
| newton       | newtons                          | force_newton                                                   |
| newton_meter | newton-meters                    | moment_newton_meter                                            |


## 3. Query - Request-Reply messaging (Remote Procedure Calls)

### 3.1 Specific key-space design

For the request / reply messaging pattern, the lower level hierarchy in the key space consists of the following levels:

  `.../@rpc/{procedure}/source_id`
  
With:

* `@rpc` being the hardcoded word "@rpc" letting users directly identify key expression category. The `@`makes this a verbatim chunk and ensures it cant be mixed up with other chunks such as `pubsub`.
* `procedure`  being a well-known procedure name as defined in a protobuf service.
* `source_id` being the platform unique name of the micro-service either an keelson connector or processor, may contain any number of additional levels (i.e. forward slashes `/`) ei. camera/mono/0 or lidar/0

### 3.2 Interface specification

Zenoh supports a generalized version of Remote Procedure Calls, namely [queryables](https://zenoh.io/docs/manual/abstractions/#queryable). This is leveraged for Request/Response messaging (RPC) in keelson with the following additional decrees:

* All RPC endpoints (queryables) should be defined by a protobuf service definition and thus accept Requests and return Responses in protobuf format.
* All RPC endpoints (queryables) should make use of the common [`ErrorResponse`](https://github.com/RISE-Maritime/keelson/interfaces/ErrorResponse.proto) return type and the `reply_err` functionality in zenoh to propagate errors from callee to caller.

## 4. Message definition specification

Most messages include a timestamp field, following the [Google Protobuf Timestamp specification](https://protobuf.dev/reference/protobuf/google.protobuf/#timestamp). The primary timestamp represents the system time of the logging computer. If synchronization with, or tracking of, other timekeeping devices or systems is logged with subject `time`.

## 5. Liveliness key-space convention

Keelson uses [Zenoh liveliness tokens](https://zenoh.io/docs/manual/liveliness/) to provide coarse-grained presence detection for sources (Layer 1 of the health monitoring architecture). A liveliness token signals that a source process is running and may produce output on any subject.

### 5.1 Token key format

Each source declares a single liveliness token using a wildcard (`*`) in the subject position:

```
{base_path}/@v0/{entity_id}/pubsub/*/{source_id}
```

For example, a GNSS source on the entity `landkrabban`:

```
keelson/@v0/landkrabban/pubsub/*/gnss/0
```

The `*` in the subject position means "this source is alive and may produce output on any subject." It is a presence signal, not a capability declaration — the token does not specify which subjects the source actually publishes.

> **NOTE:** Zenoh treats `*` in a token declaration as a pattern. This means the token will match any concrete subject query (e.g., a query for `pubsub/location_fix/gnss/0` will match the token `pubsub/*/gnss/0`). This is intentional — it allows presence to be discovered alongside subject-specific queries. Future versions may introduce concrete per-subject tokens for fine-grained capability declarations.

### 5.2 Subscriber key patterns

To monitor presence of all sources within an entity:

```
{base_path}/@v0/{entity_id}/pubsub/**
```

To monitor presence across all entities:

```
{base_path}/@v0/**/pubsub/**
```

A liveliness subscriber on these patterns will receive join and leave events as sources declare and undeclare their tokens.

### 5.3 Querying live tokens

To retrieve all currently live tokens for an entity:

```python
replies = session.liveliness().get("keelson/@v0/landkrabban/pubsub/**")
for reply in replies:
    print(reply.ok.key_expr)  # e.g. keelson/@v0/landkrabban/pubsub/*/gnss/0
```

### 5.4 Verbatim chunk isolation

The `@v0` verbatim chunk guarantees that liveliness tokens and subscribers for different major versions are isolated from each other. A subscriber on `@v0/**` will never receive events from tokens declared under `@v1/**`, and vice versa. This is enforced by Zenoh's verbatim chunk matching rules (see [Section 1](#1-common-key-space-design)).
## 6. Route planning and voyage protocol

> **STATUS: PROPOSAL.** This section is the missing half of the route/voyage
> payloads (PR #141): the payloads say what a route *is*, this says how the
> messages refer to each other and what sequence of publishes constitutes a
> correct state change. Without it two implementers will not interop, because
> every rule below currently lives in a comment or nowhere.
>
> Each rule is marked **[as-built]** where an implementation already behaves
> this way (Crowsnest's `routeSync.js`), or **[proposed]** where it is being
> settled here for the first time. The distinction matters: as-built rules are
> descriptions of something that works, proposed ones are open to argument.

### 6.1 Why this section exists

Everything else in this specification is *stateless and single-message*: a key
identifies a value, a payload carries it, and nothing depends on what was
published before. Route planning is the first feature in keelson that is
**stateful and multi-message** — it has editions, a lease, an audit trail, and a
plan/execution join. None of those work without agreement on questions that a
`.proto` file cannot express.

### 6.2 Reference model

Cross-message references are **stable identifiers, never array indices**.
**[proposed]**

An index into `Route.waypoints` means a different waypoint the moment a
waypoint is inserted or removed, and editions exist precisely so that routes can
change. Anything addressing a waypoint MUST use `Waypoint.id`.

A reference to a route is a **pair**: the route id and the edition it was
resolved against. A bare `route_id` is not a reference — it names a lineage, not
a document.

```text
RouteRef { route_id, route_edition_number }
```

Consumers holding a `RouteRef` resolve it via §6.3. A reference whose edition
cannot be resolved MUST be treated as dangling and surfaced, not silently
resolved to `latest` — an execution pinned to edition 4 that quietly follows
edition 7 is the failure this rule exists to prevent.

### 6.3 The edition store

The editioning scheme presupposes somewhere to fetch a prior edition from. That
store is the key tree itself. **[as-built]**

```text
{base_path}/@v0/{entity_id}/pubsub/route/{route_id}/edition/{N}   keelson.Route
{base_path}/@v0/{entity_id}/pubsub/route/{route_id}/latest        keelson.Route
```

Both keys carry the same `subject` (`route`); the discriminator lives in the
`source_id` position, which §2.1 permits to be multi-segment. `latest` is a
pointer: it holds a copy of the highest-numbered edition, so a consumer that
does not care about history subscribes to `route/*/latest` and never thinks
about editions at all.

Resolving `RouteRef{r, N}` is therefore a `get` on `route/{r}/edition/{N}`.

**Durability is a router responsibility, not a payload one.** **[as-built]**
Which subjects survive a restart is configured in the Zenoh router's
`storage_manager`, not encoded in a separate `state/` key tree:

| Key | Persisted | Cardinality |
|---|---|---|
| `route/{route_id}/edition/{N}` | yes | one per edition, immutable once written |
| `route/{route_id}/latest` | yes | one per route |
| `voyage/{voyage_id}` | yes | one per voyage |
| `route_change_event/{route_id}/{change_id}` | yes | append-only, one per bump |
| `route_status/{route_id}` | no | latest wins |
| `route_edit_authority/{route_id}` | no | latest wins |
| `route_edit_request/{route_id}` | no | transient |
| `route_execution/{voyage_id}` | no | 1 Hz, latest wins |

An edition key, once written, MUST NOT be rewritten. Editions are the audit
trail; a mutable edition is not one.

### 6.4 Choreography: edition bump

Publishing a change to a route is **three publishes**, in this order.
**[as-built]**

```text
1. route/{route_id}/edition/{N+1}      the new edition (immutable)
2. route/{route_id}/latest             pointer moves to N+1
3. route_change_event/{route_id}/{cid} what changed, who, why
```

Ordering is load-bearing: the edition must exist before `latest` points at it,
or a consumer that follows the pointer resolves a key that is not there yet.

**There is exactly one audit channel.** **[proposed]** `RouteInfo.change_history`
and `RouteChangeEvent` are two records of the same events that can disagree, and
`change_history` additionally grows without bound inside a message that is
re-sent in full on every publish. `RouteChangeEvent` is the truth;
`change_history` should be removed. The reference implementation already writes
it that way: `bumpEdition()` emits the event and never appends to the embedded
history. It still *renders* `change_history` when some other producer has
populated it — which is precisely the divergence removing the field ends.

Not atomic, and deliberately not presented as such: a publisher that dies
between steps 1 and 2 leaves an orphan edition, which is inert and detectable
(an edition higher than `latest`), rather than a corrupt pointer.

### 6.5 Choreography: the edit lease

`route_edit_authority` is a **cooperative single-writer lease**, not a security
boundary. **[as-built]**

```text
take     publish RouteEditAuthority{holder, token, granted_at, expires_at}
hold     re-publish every HEARTBEAT_INTERVAL_SECONDS (10)
expire   lease is void at expires_at; TTL is LEASE_TTL_SECONDS (30)
release  publish a release carrying the SAME token
```

Rules that a reader cannot infer from the payload:

* A release MUST carry the token it was granted with — otherwise any station can
  release any other station's lease by accident.
* The token is **self-issued**. The lease is advisory: it prevents two
  well-behaved editors from colliding, and prevents nothing else. Anything
  needing a real access boundary must not rely on it.
* `lease_ttl_seconds` and `heartbeat_interval_seconds` are carried for the
  holder's benefit but are **redundant** with `granted_at`/`expires_at` and can
  disagree with them. **[proposed]** `expires_at` is authoritative.

**Open — clock skew.** `expires_at` is wall-clock from the granting site, and
every subscriber compares it against its own clock. Two sites a few seconds
apart will disagree about when a lease ended. This is unresolved; see §6.7.

### 6.6 Choreography: voyage activation

**Execution lifecycle lives on `VoyageStatus` alone.** **[as-built]**

A route is a *plan*; a voyage is an *execution of a plan*. `Route.status`
describes the plan's authoring/quality state and MUST NOT be moved to reflect
that a voyage is running. Activating a voyage therefore does **not** touch the
route:

```text
1. voyage/{voyage_id}         Voyage{status=VOYAGE_STATUS_ACTIVE, route_ref}
2. route_execution/{voyage_id} begins at 1 Hz, published by the vessel
```

This is what removing the `route_active` / `voyage_active` subjects (§2 of the
review) made expressible: with lifecycle in exactly one field, there is no
second place for it to disagree with itself.

### 6.7 Open questions

Named rather than papered over. Each needs a decision before the feature can be
called interoperable:

1. **Lease clock skew** (§6.5) — comparing a remote `expires_at` against a local
   clock. Options: require synchronised time, carry a duration instead of an
   instant, or have each subscriber track the lease from its own receipt time.
2. **Signature canonicalisation** — `RouteSignature` signs *something*, but
   protobuf serialisation is not canonical, so no two implementations can be
   relied upon to produce the same bytes. Signatures are unverifiable across
   implementations until a canonical signing input is defined.
3. **`google.protobuf.Any`** in `Route.extensions` and `RouteChangeEvent.diff` —
   undecodable through the subject registry, which is how everything else on the
   bus is self-describing. A deliberate exception per #154, re-stated here as a
   known cost.
4. **The produce/validate RPC is absent.** `interfaces/` has no route service, so
   the primary means by which a `Route` comes into existence is undefined. A
   second implementer of a route planner has nothing to conform to.
5. **A third waypoint vocabulary.** `keelson.Waypoint` (on `foxglove.LocationFix`)
   is parallel to `interfaces/VehicleMission.proto`'s `Waypoint` (on
   `Coordinate`). Converging is cheaper before consumers harden against this one.
