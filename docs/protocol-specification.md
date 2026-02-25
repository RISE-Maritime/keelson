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

### 2.2 Message format specification

Each message published to zenoh must be a protobuf-encoded keelson `Envelope`. An `Envelope` contains exactly one (1) `payload`, we say that a `payload` is **enclosed** within an `Envelope` by the publisher and can later be **uncovered** from that `Envelope` by the subscriber. 

[sketch](./subject_payload_schema.drawio.svg)

Keelson support a set of well-known `payload`s, defined by the protobuf schemas available in [messages](https://github.com/RISE-Maritime/keelson/messages/payloads/). Each well-known `payload` is associated with an informative `subject`, the mapping between `subject`s and `payload`s is maintained in a [look-up table in YAML format](https://github.com/RISE-Maritime/keelson/messages/subjects.yaml).

The main design principles behind this scheme are:

* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with one or more subjects that describes how to interpret the **information**.
* Each subject or procedure is part of the key when publishing data to zenoh, refer to the section about [keys](#21-specific-key-space-design), this helps the sender and receiver to put the information into a **context**.

#### 2.2.1 Naming convention for `subject`s category

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