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

Most messages include a timestamp field, following the [Google Protobuf Timestamp specification](https://protobuf.dev/reference/protobuf/google.protobuf/#timestamp). The primary timestamp represents the system time of the logging computer. If synchronization with, or tracking of, other timekeeping devices or systems is required, 

....