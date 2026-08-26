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

**Coordinates: observation vs referent.**
A latitude/longitude pair is modelled by *what it claims*, not by which transport carries it:

* An **observation** — where a sensor says something *is* (a position fix, a tracked target) — uses `foxglove.LocationFix`. Covariance, `frame_id` and fix quality are properties a measurement genuinely has; this is what telemetry subjects like `location_fix` carry.
* A **referent** — where something is *intended, planned or defined* to be (a mission waypoint, a route leg, a geofence vertex, a home position) — uses the shared domain types `keelson.Coordinate` / `keelson.Waypoint` (`messages/payloads/`). An intention has no covariance; building plan types on `LocationFix` forces producers to fabricate measurement metadata and consumers to guess what it means there.

> **The test:** *does this position carry measurement context (covariance, frame, fix quality) that a consumer could act on?* **Yes** → observation → `foxglove.LocationFix`. **No** → referent → `keelson.Coordinate`. The transport is irrelevant — both types are legal on pubsub and RPC alike; there is exactly one `Coordinate` in the system, and every interface or subject that needs a referent position references it rather than declaring its own. Waypoints are the level above and there are deliberately two — `keelson.Waypoint` (route plan artifact) and `keelson.MissionWaypoint` (autopilot mission step); both build on the one `Coordinate`. See §6.8 item 5.

> **NOTE:** free map rendering in Foxglove Studio is not a reason to model a plan as `LocationFix`. Conversion for display belongs at the visualization edge (e.g. the Foxglove bridge converting a route to foxglove types for drawing), not in the domain model.

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
| Unit Symbol  | Full Unit Name                     | Example Subjects Using It                                                |
|--------------|------------------------------------|--------------------------------------------------------------------------|
| m            | meter                              | location_fix_accuracy_horizontal_m, draught_mean_m, altitude_above_msl_m |
| mm           | millimeter                         | precipitation_accumulation_mm                                            |
| deg          | degree (angle)                     | heading_true_north_deg, roll_deg, target_bearing_relative_deg            |
| degps        | degrees per second                 | roll_rate_degps, yaw_rate_degps                                          |
| knots        | nautical miles per hour            | speed_over_ground_knots, speed_through_water_knots                       |
| pct          | percent                            | engine_throttle_pct, wheel_position_pct, battery_state_of_charge_pct     |
| rpm          | revolutions per minute             | propeller_rate_rpm, engine_rate_rpm                                      |
| celsius      | degrees Celsius                    | engine_oil_temperature_celsius, air_temperature_celsius                  |
| psi          | pounds per square inch             | engine_oil_pressure_psi, engine_coolant_pressure_psi                     |
| lph          | liters per hour                    | engine_fuel_rate_lph                                                     |
| l            | liters                             | engine_fuel_consumed_l                                                   |
| mmph         | millimeters per hour               | precipitation_rate_mmph                                                  |
| v            | volts                              | battery_voltage_v, battery_min_voltage_v, analog_voltage_v               |
| a            | amperes                            | battery_current_a                                                        |
| ah           | ampere-hours                       | battery_capacity_ah, battery_current_consumed_ah                         |
| wh           | watt-hours                         | battery_energy_consumed_wh                                               |
| pa           | pascal                             | air_pressure_pa                                                          |
| ppt          | parts per thousand                 | water_salinity_ppt                                                       |
| mps          | meters per second                  | true_wind_speed_mps, climb_rate_mps, surge_velocity_mps                  |
| mpss         | meters per second squared          | linear_acceleration_mpss, surge_acceleration_mpss                        |
| radps        | radians per second                 | angular_velocity_radps                                                   |
| gauss        | gauss (magnetic field strength)    | magnetic_field_gauss                                                     |
| lux          | lux (illuminance)                  | illuminance_lux                                                          |
| s            | seconds                            | heave_period_s, target_tcpa_s, battery_time_remaining_s                  |
| newton       | newtons                            | force_newton                                                             |
| newton_meter | newton-meters                      | moment_newton_meter                                                      |
| db           | decibels (dimensionless ratio)     | radio_rsrq_db, radio_sinr_db                                             |
| dbm          | decibels relative to one milliwatt | radio_rssi_dbm, radio_rsrp_dbm, radio_tx_power_dbm                       |
| mhz          | megahertz                          | radio_downlink_bandwidth_mhz, radio_uplink_bandwidth_mhz                 |
| bps          | bits per second                    | radio_downlink_bitrate_bps, radio_uplink_bitrate_bps                     |


## 3. Query - Request-Reply messaging (Remote Procedure Calls)

### 3.1 Specific key-space design

For the request / reply messaging pattern, the lower level hierarchy in the key space consists of the following levels:

  `.../@rpc/{interface}/{version}/{procedure}/{source_id}`

With:

* `@rpc` being the hardcoded word "@rpc" letting users directly identify key expression category. The `@` makes this a verbatim chunk and ensures it cant be mixed up with other chunks such as `pubsub`.
* `interface` being a well-known RPC interface name (snake_case), registered in [`interfaces.yaml`](https://github.com/RISE-Maritime/keelson/blob/main/messages/interfaces.yaml).
* `version` being the interface version chunk, in the form `v{N}` where `N` is a positive integer (`v1`, `v2`, ...). This is a regular (non-verbatim) chunk; wildcards may cross it, which is precisely what discovery clients need to enumerate live interfaces across versions with a single subscription. Isolation between versions in practice is enforced by callers always pinning a specific version when issuing a call.
* `procedure` being the procedure (method) name (snake_case), as defined in the protobuf service for this interface version.
* `source_id` being the platform unique name of the micro-service either an keelson connector or processor, may contain any number of additional levels (i.e. forward slashes `/`) ei. camera/mono/0 or lidar/0

**Example:**

```
keelson/@v0/landkrabban/@rpc/vehicle_lifecycle/v1/arm/mavlink/0
```

### 3.2 Interface specification

Zenoh supports a generalized version of Remote Procedure Calls, namely [queryables](https://zenoh.io/docs/manual/abstractions/#queryable). This is leveraged for Request/Response messaging (RPC) in keelson with the following additional decrees:

* All RPC endpoints (queryables) should be defined by a protobuf service definition and thus accept Requests and return Responses in protobuf format.
* All RPC endpoints (queryables) should make use of the common [`ErrorResponse`](https://github.com/RISE-Maritime/keelson/interfaces/ErrorResponse.proto) return type and the `reply_err` functionality in zenoh to propagate errors from callee to caller.

### 3.3 The interfaces.yaml registry

The file [`messages/interfaces.yaml`](https://github.com/RISE-Maritime/keelson/blob/main/messages/interfaces.yaml) catalogs the well-known RPC interfaces of the current keelson release, structurally analogous to `subjects.yaml` for pub/sub. It is a flat map from `{interface}/{version}` keys (mirroring the wire chunks under `@rpc`) to the full name of the protobuf service defining that interface version.

Versioning rules:

* **Day-one versioning.** Every interface is published with a version chunk from initial publication; `v1` is the version for newly-published interfaces. There are no unversioned interfaces.
* **No version suffix in service names.** The service for `vehicle_lifecycle/v1` is `VehicleLifecycle`, not `VehicleLifecycleV1`. Versioning is tracked in `interfaces.yaml`; the proto file at a given release tag is implicitly the schema for the version listed in that release's `interfaces.yaml`. A backward-incompatible change to an interface `.proto` MUST be accompanied by a version bump in `interfaces.yaml`.
* **Single version per release.** A given release of the keelson specification contains exactly one version of each well-known interface. Multiple versions MAY coexist on the bus at runtime as legacy connectors from prior releases remain in operation; prior versions of a `.proto` file are recoverable from git history at the corresponding release tag.
* **Deprecation by removal.** Removal of an interface version from `interfaces.yaml` in a new release is the deprecation signal. No explicit deprecated flag is carried; operators consult release notes for migration guidance.
* **Schema retention is an integration concern.** Consumers needing to talk to multiple versions of an interface during a transition (e.g. a fleet tool talking to both v1 and v2 connectors during a rolling upgrade) source the older schema themselves — pin a checkout of the prior keelson release or vendor the relevant `.proto`, generate code for each version in distinct namespaces, and dispatch on the version chunk parsed from discovery. The cost of multi-version interoperability sits with the consumer by design.

### 3.4 Per-version immutability

Once an interface version is published in a keelson release, its protobuf schema MUST NOT change in a backward-incompatible way. The following constitute breaking changes and require a new version:

* Adding a procedure to the service
* Removing a procedure from the service
* Renaming a procedure
* Changing the request or response type of a procedure
* Changing the semantics of a procedure

Additive proto changes within an existing version (adding optional fields to request or response messages, where protobuf's own backward compatibility rules apply) are permitted as long as the wire-level interaction remains backward-compatible.

> **NOTE:** Adding a procedure is considered a breaking change because consumers that know about the new procedure cannot distinguish, from the wire, between "the implementor predates this method" and "the implementor is unreachable" — both produce no-reply outcomes from Zenoh. New procedures therefore require a version bump so that consumers can detect compatibility from the version chunk before issuing a call.

**Mutability carve-out under `@v0`:** while the keelson protocol sits at `@v0` (explicitly marked as unstable), interface schemas MAY change without version bumps. The immutability requirements apply from `@v1` onwards. Implementations under `@v0` accept the corresponding instability.

### 3.5 RPC discovery via interface-level liveliness

Each source serving one or more RPC interfaces MUST declare one Zenoh liveliness token per `(interface, version)` pair it serves:

```
{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/*/{source_id}
```

The `*` in the procedure slot follows the keelson convention for "any procedure in this scope". Tokens MUST be declared when the corresponding queryables become available (typically session open) and are undeclared automatically on session close (clean or crashed); a source MUST NOT hold a token for an interface it does not currently serve.

Discovery clients subscribe to or query liveliness with patterns such as:

| Intent | Pattern |
|--------|---------|
| All live RPC endpoints on the bus | `{base_path}/@v0/*/@rpc/**` |
| All RPC endpoints on a specific entity | `{base_path}/@v0/{entity_id}/@rpc/**` |
| All versions of a specific interface, any entity | `{base_path}/@v0/*/@rpc/{interface}/*/**` |
| One specific version of an interface, any entity | `{base_path}/@v0/*/@rpc/{interface}/{version}/**` |

For each liveliness sample, the client parses the key into `(entity_id, interface, version, source_id)` and resolves the protobuf service via `interfaces.yaml`.

> **NOTE:** zenoh wildcards never match verbatim chunks, so a pattern must spell out `@rpc` literally — `{base_path}/@v0/**` does NOT match RPC liveliness tokens. Also note the `**` tails: `source_id` may contain multiple chunks, so single-`*` tails would miss most sources.

### 3.6 Full-interface implementation rule

A source advertising an interface (i.e. holding the corresponding liveliness token) MUST respond to every procedure defined in that interface version. Where the source cannot meaningfully implement a procedure, it MUST return a typed response indicating the limitation, never silence. Two cases are distinguished, and the distinction is load-bearing for discovery clients and operator UIs:

* **`COMMAND_RESULT_UNSUPPORTED`** — the source *structurally* does not implement this procedure; the answer will not change for the lifetime of this source instance. Consumers SHOULD NOT retry and SHOULD present the procedure as permanently unavailable on this source.
* **`COMMAND_RESULT_DENIED`** — the source could in principle handle the procedure but is refusing under current conditions (policy, vehicle state, transient constraints). The answer MAY change; consumers MAY retry under different conditions, and UIs should keep the procedure callable with the reason surfaced.

A source MUST NOT return DENIED for a procedure it can never fulfill, nor UNSUPPORTED for one it could fulfill under different conditions. For interfaces whose responses don't carry `CommandResult`, the equivalent signal is an `ErrorResponse` on `reply_err` with an appropriate code.

The intent: a consumer that sees an interface liveliness token can call any procedure in that interface with confidence that it will receive a *typed* reply. Absence of a reply on a procedure key whose interface is currently advertised indicates either a protocol violation by the implementing source or a transport-level failure (partition, timeout) — the wire does not distinguish the two, so consumers should treat persistent no-reply as a fault to surface, not silently retry forever.

The natural design pressure: keep interfaces cohesive and small. Before adding a procedure to an existing interface, apply the co-implementation test — would every existing source serving this interface be able to implement the new procedure meaningfully, including a clean UNSUPPORTED reply? If not, it belongs in a separate interface. If a subset of procedures would naturally be implemented by a different *kind* of source than the rest, split the interface rather than forcing sources to advertise a surface that is mostly UNSUPPORTED noise.

## 4. Message definition specification

Most messages include a timestamp field, following the [Google Protobuf Timestamp specification](https://protobuf.dev/reference/protobuf/google.protobuf/#timestamp). The primary timestamp represents the system time of the logging computer. If synchronization with, or tracking of, other timekeeping devices or systems is logged with subject `time`.

## 5. Liveliness key-space convention

Keelson uses [Zenoh liveliness tokens](https://zenoh.io/docs/manual/liveliness/) for presence and capability discovery (Layer 1 of the health monitoring architecture). Liveliness is structured into three orthogonal tiers — three independent facts, three independently-declarable tokens:

| Tier | Key shape | Mandatory for | Forbidden for |
|------|-----------|---------------|---------------|
| Source-level | `{base_path}/@v0/{entity_id}/*/{source_id}` | Any process with a producing role | Pure consumers (sinks) |
| Pubsub subject-level | `{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}` | Sources that publish to keelson pubsub | Sources that publish nothing |
| RPC interface-level | `{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/*/{source_id}` | Sources that serve RPC | Sources that serve no RPC |

A "producing role" means: the process publishes pubsub data, OR serves RPC, or both. A process holds any combination of tokens consistent with its role.

### 5.1 Source-level liveliness

```
{base_path}/@v0/{entity_id}/*/{source_id}
```

Declared exactly once per producing *identity* — the `(entity_id, source_id)` pair — at session open; undeclared on shutdown (Zenoh delivers leave events automatically on session close, clean or crashed). The token states "the producer identified by `{entity_id}/{source_id}` is present on the bus" — not which category, subjects or interfaces; those are conveyed by the per-capability tokens. Most processes expose exactly one `source_id` and therefore hold exactly one source-level token; a process that publishes under several `source_id`s (e.g. one poller fanning out per-channel identities) declares one source-level token per identity, so that consumers correlating presence per `(entity_id, source_id)` see each of them.

The `*` occupies the category slot (`pubsub`, `@rpc`, ...) because source-level presence is category-agnostic — placing it under a verbatim chunk would misrepresent its scope. Note that since `@rpc` is a verbatim chunk, the wildcard never actually intersects RPC-scoped patterns; RPC capability is discovered through the interface-level token, not through this one.

### 5.2 Pubsub subject-level liveliness

```
{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}
```

Same shape as a published pubsub key, declared as a liveliness token. One token per subject the source is currently configured or wired to publish.

**The token declares capability, not activity**: "I am configured to publish on this subject; when conditions warrant, data will appear." It commits to no publication rate and MUST NOT be retracted because data is momentarily absent — many keelson publishers are intermittent by nature (alarms, state changes), and tying token lifecycle to data flow would force arbitrary timeouts or oscillation against silent-but-healthy publishers. Whether data is currently flowing is a separate question, observable via data rate.

* **Static-capability sources** (publishable set fixed by code/config at startup — e.g. the NMEA connector's parser-supported sentence types) declare all subject tokens at session open, even if the physical installation will never produce some of them.
* **Dynamic-capability sources** (hardware enumeration, config reload) declare tokens as capabilities appear and undeclare them when the underlying capability is removed.

### 5.3 RPC interface-level liveliness

```
{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/*/{source_id}
```

One token per `(interface, version)` pair the source serves — defined in [Section 3.5](#35-rpc-discovery-via-interface-level-liveliness), together with the full-interface rule it implies.

### 5.4 Producer / consumer asymmetry

A process with a producing role MUST declare the source-level token. A pure consumer — one that only subscribes and/or issues RPC queries (sinks, recorders, visualization bridges, ad-hoc debug clients) — MUST NOT declare any liveliness token.

Producer presence is operationally meaningful to other bus participants: consumers need to discover producers. Consumer presence is not; the parties that care whether a recorder is running (the operator, the recording's downstream user) have better channels — systemd / container health checks, log shipping, output artifact inspection. The MUST NOT is deliberately strict: allowing optional consumer declarations would make "entry present" ambiguous. Strict exclusion gives the liveliness set a uniform interpretation: **every entry is a producer**.

### 5.5 Discovery query patterns

| Intent | Pattern |
|--------|---------|
| All live producers on the bus | `{base_path}/@v0/*/*/**` |
| All producers on a specific entity | `{base_path}/@v0/{entity_id}/*/**` |
| All pubsub subjects advertised by any source | `{base_path}/@v0/*/pubsub/*/**` |
| All sources advertising a specific subject | `{base_path}/@v0/*/pubsub/{subject}/**` |
| All subjects advertised by a specific source | `{base_path}/@v0/{entity_id}/pubsub/*/{source_id}` |
| All RPC interfaces | see [Section 3.5](#35-rpc-discovery-via-interface-level-liveliness) |

A received liveliness sample is classified by inspecting the chunk after the entity chunk — **not** by chunk count, since `source_id` may span multiple chunks:

* literal `*` in the category slot → source-level token
* `pubsub` + literal `*` in the subject slot → legacy coarse token (Section 5.7)
* `pubsub` + concrete subject → subject-level token
* `@rpc` → interface-level token

> **NOTE:** two zenoh matching facts shape these patterns. (1) Wildcards never intersect verbatim chunks: `{base_path}/@v0/**` does NOT receive `@rpc`-tier tokens — a discovery client needs a second subscription with a literal `@rpc` chunk (Section 3.5). (2) A single `*` matches exactly one chunk, so patterns end in `**` wherever a multi-chunk `source_id` may follow. Also note that a subscription for subject-level tokens (`.../pubsub/*/**`) additionally receives source-level and legacy coarse tokens whose own wildcard chunk intersects `pubsub` — which is why classification inspects the received key's literal chunks.

### 5.6 Querying live tokens

To retrieve all currently live pubsub-related tokens for an entity:

```python
replies = session.liveliness().get("keelson/@v0/landkrabban/pubsub/**")
for reply in replies:
    print(reply.ok.key_expr)  # e.g. keelson/@v0/landkrabban/pubsub/location_fix/gnss/0
```

### 5.7 Legacy coarse token (transition)

Before the three-tier structure, each source declared a single coarse token:

```
{base_path}/@v0/{entity_id}/pubsub/*/{source_id}
```

Connectors from prior releases may still hold this shape. During the transition window, aggregators SHOULD treat the legacy token as evidence of source-level presence and subscribe to both shapes; the legacy convention is removed once connectors of operational interest have migrated.

### 5.8 Verbatim chunk isolation

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

`keelson.RouteRef` is a real message in `Route.proto`, not a naming convention:
`Voyage` and `RouteExecution` both carry one. Two loose fields can be updated
independently, and a message that re-pinned the id but not the edition would
name an edition it is not executing.

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
| `route_signature/{route_id}/edition/{N}` | yes | one per signed edition (§6.3.1) |
| `route_status/{route_id}` | no | latest wins |
| `route_edit_authority/{route_id}` | no | latest wins |
| `route_edit_request/{route_id}` | no | transient |
| `route_execution/{voyage_id}` | no | 1 Hz, latest wins |

An edition key, once written, MUST NOT be rewritten. Editions are the audit
trail; a mutable edition is not one.

#### 6.3.1 What a route signature signs **[proposed]**

`RouteSignature` used to live inside `Route` itself, which made it impossible to
say what it signed. Signing the published bytes of a message that contains the
signature is circular. The usual escape — "serialize the message with the
signature field cleared" — requires a canonical protobuf encoding, and protobuf
does not have one: field ordering, unknown-field retention and map ordering are
all implementation-defined, so two conforming libraries can emit different bytes
for the same message. Signatures produced that way are unverifiable across
implementations, which is worse than no signatures, because they look like a
guarantee.

The rule that removes the ambiguity uses the immutability §6.3 already
establishes:

> **The signing input is exactly the `Envelope.payload` byte string stored at
> `route/{route_id}/edition/{N}`** — the serialized `keelson.Route`, byte for
> byte as its publisher wrote it.

Verification is therefore:

```text
1. get   route/{route_id}/edition/{N}        -> Envelope
2. take  Envelope.payload                    -> bytes B  (do not re-serialize)
3. get   route_signature/{route_id}/edition/{N} -> keelson.RouteSignatures
4. for each entry: verify(signature, B, key_reference, algorithm)
```

Step 2 is the whole point: the verifier never re-encodes the route, so there is
nothing for two implementations to disagree about. No canonicalization scheme,
no field-ordering rule, no "clear these fields first".

Three consequences worth stating, because each is a property implementers will
otherwise have to guess at:

* **Signatures live beside the edition, never inside it** —
  `keelson.RouteSignatures` on the `route_signature` subject, keyed to the same
  `{route_id}/edition/{N}`. `Route.signatures` (field 30) is `reserved`.
* **Signing does not bump the edition.** Attesting to a plan is not editing it.
  A second signer countersigning hours later adds an entry to
  `route_signature/…` and leaves the artifact and the change log untouched.
* **This scheme depends on editions being immutable.** If §6.3's
  "MUST NOT be rewritten" is ever relaxed, this rule has to be revisited — a
  signature over bytes that can change underneath it attests to nothing.

Unsigned editions remain perfectly legal; absence of a `route_signature` key
means "nobody has attested to this", not "invalid".

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
re-sent in full on every publish. `RouteChangeEvent` is the truth, and
`change_history` has been removed (`RouteInfo` field 60, now `reserved`). The
reference implementation already wrote it that way: `bumpEdition()` emits the
event and never appends to the embedded history. It still *renders*
`change_history` when some other producer has populated it — which is precisely
the divergence removing the field ends.

Not atomic, and deliberately not presented as such: a publisher that dies
between steps 1 and 2 leaves an orphan edition, which is inert and detectable
(an edition higher than `latest`), rather than a corrupt pointer.

### 6.5 Choreography: the edit lease

`route_edit_authority` is a **cooperative single-writer lease**, not a security
boundary. **[as-built]**

```text
take     publish {holder, token, lease_ttl_seconds, granted_at, expires_at}
hold     re-publish every heartbeat_interval_seconds (10)
expire   receiver-local: lease_ttl_seconds (30) after the last message received
release  publish a release carrying the SAME token
```

Rules that a reader cannot infer from the payload:

* A release MUST carry the token it was granted with — otherwise any station can
  release any other station's lease by accident.
* The token is **self-issued**. The lease is advisory: it prevents two
  well-behaved editors from colliding, and prevents nothing else. Anything
  needing a real access boundary must not rely on it.

#### 6.5.1 Expiry is evaluated on the receiver's own clock **[proposed]**

**A receiver MUST NOT compare `expires_at` against its own clock.** On every
authority message it accepts, a receiver arms a local deadline:

```text
deadline := local_time_of_receipt + lease_ttl_seconds
expired  := local_now > deadline
```

Every quantity in that test comes from one clock — the receiver's own — so the
result cannot be wrong because two sites disagree about the time.

This inverts the obvious reading of the payload, so it is worth being explicit
about the roles:

| Field | Role |
|---|---|
| `lease_ttl_seconds` | **The contract.** What receivers arm on. The only skew-free lease quantity on the wire. |
| `expires_at`, `granted_at`, `last_heartbeat` | The holder's own view, in the holder's clock. Display, logging and diagnostics. **Never** a receiver's expiry decision. |
| `heartbeat_interval_seconds` | Informational; the holder's refresh cadence. |

`expires_at` is wall-clock from the granting site. A receiver comparing it to
its own clock inherits the difference between the two clocks: a receiver running
five seconds fast expires a live lease five seconds early and may grant it to a
second editor while the first still believes it holds it. Nothing in the system
detects that — both sites are behaving correctly by their own reading.

Two consequences worth stating rather than discovering:

* **Network delay is safe, skew is not.** A message delayed in transit arms the
  receiver's deadline *late* by the one-way delay, so the receiver considers the
  lease busy slightly longer than the holder does. Being late to release is
  safe; being early to steal is not. The rule fails in the safe direction.
* **A receiver that hears no heartbeat expires the lease on time regardless of
  what `expires_at` said.** Expiry follows silence, which is the property the
  lease actually needs.

This rule is not specific to route editing. It applies to every keelson lease
of this shape — `route_edit_authority` and `command_authority` share it, and the
latter guards actuation, where two holders is a materially worse outcome than a
route edited twice.

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

### 6.7 How a route comes into existence: the planner RPC

Everything above describes routes that already exist. The mechanism that
*produces* one is an RPC service, defined in **`interfaces/RoutePlanner.proto`**
and registered as `route_planner/v1`. The reference implementation is
`keelson-processor-route-planner`.

Four procedures, on the RPC key space of §3:

```
{base_path}/@v0/{entity_id}/@rpc/route_planner/v1/{procedure}/{source_id}
```

| Procedure | Request | Reply |
|---|---|---|
| `plan_route` | `PlanRouteRequest` — ordered `Coordinate`s, vessel constraints, an optimization goal, an alternatives bound | **one `keelson.Route` per alternative** |
| `validate_route` | a `keelson.Route` to check | the same route with `challenges` / `issues` / `status` populated |
| `get_route` | `keelson.RouteRef` | the stored `keelson.Route` |
| `select_route` | `keelson.RouteRef` | the stored route with `status` advanced and the edition bumped |

Failures reply `keelson.interfaces.ErrorResponse` via `reply_err`, per §3.2.

Requests and replies are **bare protobuf, not Envelope-wrapped**. §3.2 says only
"protobuf format" and §2.2's enclose/uncover rule is scoped to pub/sub, so this
was left to be inferred; it is stated here and in the proto so the next
implementer does not have to guess.

Three properties the service definition cannot carry, which an implementer will
otherwise get wrong:

* **`plan_route` is a multi-reply.** Alternatives are returned by calling reply
  once per alternative on the same key, not by wrapping them in a list message.
  Because they share a key, **callers MUST query with `consolidation=NONE`** or
  Zenoh collapses the set to a single reply and the caller silently sees one
  alternative. Each alternative is also published on `route` and stored, so
  `get_route` can fetch any of them afterwards.
* **`select_route` mutates.** It is the one procedure here that bumps an edition,
  so it triggers the §6.4 choreography — the reply is not the whole effect.
* **A request is not a route.** `plan_route` once took a `keelson.Route`
  "template", with start/end/via smuggled in as waypoints and the planner's own
  inputs as stringly-typed keys inside an `Extensions` entry. A request has no
  id, no edition, no status and nothing to sign, so it is now its own type.
  `RouteInfo` is still reused for the constraints — those genuinely are the same
  field set a produced route carries, which lets a caller re-plan from an
  existing route's constraints.
### 6.8 Decisions on the questions this section opened

These were listed as open questions in the first draft of §6. All five are now
settled. They are kept here, with their reasoning, because a decision whose
argument is lost gets relitigated — and because two of them constrain future
work rather than ending it.

1. **Lease clock skew — receivers arm their own deadline.** A receiver MUST NOT
   compare `expires_at` to its own clock; it arms
   `local_time_of_receipt + lease_ttl_seconds` on every accepted message. See
   §6.5.1. A duration is the only lease quantity on the wire that survives two
   sites disagreeing about the time.

2. **Signature canonicalisation — sign the stored edition bytes.** There is no
   canonical protobuf encoding to define, and §6.3.1 does not try to invent one:
   the signing input is the `Envelope.payload` bytes already stored at
   `route/{route_id}/edition/{N}`, which §6.3 guarantees are immutable.
   Signatures moved out of `Route` into `keelson.RouteSignatures` on the
   `route_signature` subject, because a signature list inside the document it
   signs is circular.

3. **`google.protobuf.Any` — kept for vendor extensions, removed everywhere
   else.** The two uses were not equivalent, so they did not get the same
   answer. `Route.extensions` **keeps** it: RTZ 1.2 / S-421 is an interchange
   format that must carry vendor data keelson does not model, which is the
   documented-leak case the interface design principles allow. It comes with the
   obligation that a consumer which cannot resolve a `type_url` passes the entry
   through unchanged rather than dropping it, so a round-trip stays lossless.
   `RouteChangeEvent.diff` was **removed** (field 10 `reserved`): it was
   convenience, not a format requirement, and an audit record no independent
   consumer can decode is not an audit record. `change_summary` already carries
   the readable one.

4. **The planner RPC — now `interfaces/RoutePlanner.proto`.** **[settled]** This
   was deferred on the grounds that an interface could not import
   `keelson.Route`, because the two proto trees were compiled with only their
   own directory on the include path. #153 landed as #169 and removed that
   restriction: `messages/payloads` is on the interfaces include path, and the
   generators rewrite payload imports so a shared type is generated once and
   referenced, not registered twice. `interfaces/VehicleMission.proto` already
   relied on it. The service is registered as `route_planner/v1`.

   The shapes were not transcribed as-built. `plan_route` took a `keelson.Route`
   "template" and `get_route` / `select_route` took a `Route` carrying only an
   id — an overloaded request is exactly what *Closed sets over opaque escape
   hatches* argues against. They are now `PlanRouteRequest` and
   `keelson.RouteRef`; the latter addresses an edition, which an id alone could
   not. This breaks `keelson-processor-route-planner`, which has to move to the
   versioned RPC key shape anyway.

5. **The two waypoint types stay separate; the coordinate is what converges.**
   **[settled]** `keelson.Waypoint` is a plan artifact — stable id, revision,
   turn radius, wheel-over distance, operational context, outgoing leg. The
   autopilot mission item — position, altitude, acceptance radius, hold time —
   is the other. They are not two spellings of one concept, and merging them
   would produce a union that serves neither. The genuine duplication is one
   layer down: three geographic-position representations
   (`foxglove.LocationFix`, `interfaces/Coordinate`, and the lat/lon inside
   `LocationFix`).

   #153 landed as #169 and settled both halves. The mission item moved out of
   `interfaces/VehicleMission.proto` into the shared domain-type pool
   (`messages/payloads/Mission.proto`) and is named **`keelson.MissionWaypoint`**
   — it could not keep the bare `Waypoint`, which is this section's plan
   artifact. Both now carry `keelson.Coordinate` for position: this section's
   `Waypoint.position` moved off `foxglove.LocationFix`, since a planned
   waypoint is a referent, not a measurement, and has no use for a covariance
   matrix or fix quality.

   Still on `foxglove.LocationFix` and arguably mis-typed by the same test:
   `CircleGeometry.centre`. A defined circle centre is a referent too. Left as
   found rather than widened here.

Item 4 waits on the service definition landing alongside the unified proto
trees; item 5 is settled. Both were symptoms of `messages/` and `interfaces/`
being separate worlds that cannot refer to each other, which is what #153
fixed.
