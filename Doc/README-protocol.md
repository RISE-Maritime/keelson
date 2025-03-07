# The keelson protocol

In short, keelson has opinions about:

* The format of the key used when publishing data to zenoh
* The format of the data published to zenoh
* The format of the key used when declaring a queryable (i.e. RPC endpoint) in zenoh
* The format of the requests and responses exchanged via a queryable (i.e. RPC endpoint) in zenoh

**What is Zenoh?**

In order to ease the introduction to keelson, make sure you are aquainted with zenoh. The following are some good resources:

* [What is Zenoh?](https://zenoh.io/docs/overview/what-is-zenoh/)
* [Zenoh in action](https://zenoh.io/docs/overview/zenoh-in-action/)
* [The basic abstractions](https://zenoh.io/docs/manual/abstractions/)
* [Zenoh: Unifying Communication, Storage and
Computation from the Cloud to the Microcontroller](https://drive.google.com/file/d/1ETSLz2ouJ2o9OpVvEoXrbGcCvpF4TwJy/view?pli=1)

## 1. Common key-space design

In zenoh, both pub/sub and req/rep (queryables) messaging patterns all live in the same shared key "space". In keelson, the shared key-space has a common base hierarchy of three (3) levels:

`{realm}/v{major_version}/{entity_id}/...`

With:

* `realm` being a unique id for a domain/realm
* `v{major_version}` is the major version of keelson used
* `entity_id` being a unique id representing an entity within the realm (Normally the platform name ei. landkrabban, masslab, logging_pc_one)
* `...` are specific key levels depending on the messaging pattern, these are further described below.

> **NOTE:** Without exceptions, keys should adhere to `snake_case` style.

### Publish, Subscribe & RPC (Queryable)

RPC stands for remote procedure call and refers to the queryables in zenoh. So both connectors and processors can use both pubsub and rpc (queryables) depending on how the api is designed. I mainly think that connectors are applications that connect to external resources (pubsub and/or rpc depending on how the api looks) and processors are applications that transform already available data (from pubsub/rpc and to pubsub/rpc depending on how the api :et is designed).

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

![sketch](./subject_payload_schema.drawio.svg)

Keelson support a set of well-known `payload`s, defined by the protobuf schemas available in [messages](./messages/payloads/). Each well-known `payload` is associated with an informative `subject`, the mapping between `subject`s and `payload`s is maintained in a [look-up table in YAML format](./messages/subjects.yaml).

The main design principles behind this scheme are:

* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a subject that describes how to interpret the **information**.
* Each subject or procedure is part of the key when publishing data to zenoh, refer to the section about [keys](#21-specific-key-space-design), this helps the sender and receiver to put the information into a **context**.

#### 2.2.1 Naming convention for `subject`s category

There are three distinct kind of payloads that has to be covered by a naming convention for `subject`s:

* **raw** "arbitrary bytes", where we do not know the schema or do not want to express the schema as a protobuf type, these all fall under the special subject `raw` using the payload type [`TimestampedBytes`](./messages/payloads/TimestampedBytes.proto)
* **primitive payloads**, which have a specific meaning but where the protobuf type is generic, i.e [`TimestampedFloat`](./messages/payloads/TimestampedFloat.proto) or similar. In this case the subject needs to be very informative with regards to that value and we employ the following convention: `<entity>_<property>_<unit>` where `entity`, `property` and `unit` are constrained to alphanumeric characters. For example `rudder_angle_deg`.
* **complex payloads**, which have a specific protobuf type that is not shared with any other subject. In this case, the subject name should be the snake_case version of the protobuf message name, for example `RawImage` -> `raw_image`.

In general, [`subjects.yaml`](./messages/subjects.yaml) contains the current well-known subjects and can be regarded as the style-guide to follow.

## 3. Query - Request-Reply messaging (Remote Procedure Calls)

### 3.1 Specific key-space design

For the request / reply messaging pattern, the lower level hierarchy in the key space consists of the following levels:

  `.../rpc/{procedure}/{subject_in}/{subject_out}/source_id`
  
With:

* `rpc` being the hardcoded word "rpc" letting users directly identify key expression category  
* `subject_in`  being a well-known subject describing the information contained within the payload published on this key expressoinom for a input to a rpc call, same subjects as pubsub
* `subject_out`  being a well-known subject describing the information contained within the payload published on this key expressoin for a outbut to a rpc call, same subjects as pubsub
* `source_id` being the platform unique name of the micro-service either an keelson connector or processor, may contain any number of additional levels (i.e. forward slashes `/`) ei. camera/mono/0 or lidar/0

### 3.2 Interface specification

Zenoh supports a generalized version of Remote Procedure Calls, namely [queryables](https://zenoh.io/docs/manual/abstractions/#queryable). This is leveraged for Request/Response messaging (RPC) in keelson with the following additional decrees:

* All RPC endpoints (queryables) should be defined by a protobuf service definition and thus accept Requests and return Responses in protobuf format.
* All RPC endpoints (queryables) should make use of the common [`ErrorResponse`](./interfaces/common/ErrorResponse.proto) return type and the `reply_err` functionality in zenoh to propagate errors from callee to caller.
