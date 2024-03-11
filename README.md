# keelson

> **NOTE**: keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

keelson is an opinionated key-space design, pub-sub message format and generic RPC interfaces on top of [zenoh](https://github.com/eclipse-zenoh/zenoh) primarily targeting the area of Maritime Robotics. It is provided as free and open-source software under the Apache 2.0 License.

TODO: A nice graphic here would be nice...


**Repository structure**

This repository is a mono-repo. It contains the following (in order of ):

* A description of [the keelson protocol](#the-keelson-protocol)
* The well-known message schemas supported by keelson: ([messages/](./messages/README.md))
* Generic RPC interface definitions for some use cases: ([interfaces/](./interfaces/README.md))
* Connector implementations towards a multitude of sensors, middlewares and file formats: ([connectors/](./connectors/README.md))
* Software Development Kits (SDKs) for several languages ([sdks](./sdks/README.md))
* A [zenoh-cli](https://github.com/MO-RISE/zenoh-cli) codec plugin for keelson data. Bundled with the python SDK.


Releases from this repository consists of two artifacts:

* The SDKs are published to the respective language specific package repositories, see [sdks](./sdks/README.md) for details.
* A docker image containing all the [connectors](./connectors/README.md) is published to Githubs container registry



## The keelson protocol

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

### 1. Keys

In zenoh, both pub/sub and req/rep (queryables) messaging patterns all live in the same shared key "space". In keelson, the shared key-space has a common base hierarchy of three (3) levels:

`{realm}/v{major_version}/{entity_id}/...`

With:
* `realm` being a unique id for a domain/realm
* `v{major_version}` is the major version of keelson used
* `entity_id` being a unique id representing an entity within the realm

> **NOTE:** Without exceptions, keys should adhere to `snake_case` style.

#### 1.1 Pub/Sub messaging

For pub/sub messaging, the lower levels of the key hierarchy has the following levels:

  `.../data/{subject}/{source_id}`

With
  * `data` being the hard-coded word data.
  * `subject` being a well-known subject describing the information contained within the payloads published to this key. The concept of subjects is further described under Data format below. 
  * `source_id` being a unique id for the source producing the information described by `subject`. `source_id` may contain any number of addititional levels (i.e. forward slashes `/`)

#### 1.2 Req/Rep messaging (Remote Procedure Calls (RPC))

For the req/rep messaging pattern, the lower level hierarchy in the key space consists of the following levels:

  `.../rpc/{responder_id}/{procedure}`

With:
  * `rpc` being the hardcoded word rpc.
  * `responder_id` being a unique id for the responder that provides the remote procedure. `responder_id` may contain any number of addititional topic levels (i.e. forward slashes `/`)
  * `procedure` being a descriptive name of the procedure

### 2. Data format

Throughout, keelson adopts [Protocol Buffers](https://protobuf.dev/) for data serialization.

#### 2.1 Pub/Sub messaging

Each message published to zenoh must be a protobuf-encoded keelson `Envelope`. An `Envelope` contains exactly one (1) `payload`, we say that a `payload` is **enclosed** within an `Envelope` by the publisher and can later be **uncovered** from that `Envelope` by the subscriber. 

![sketch](subject_payload_schema.drawio.svg)

Keelson support a set of well-known `payload`s, defined by the protobuf schemas available in [messages](./messages/payloads/). Each well-known `payload` is associated with an informative `subject`, the mapping between `subject`s and `payload`s is maintained in a [look-up table in YAML format](./messages/subjects.yaml).

The main design principles behind this scheme are:
* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a subject that describes how to interpret the **information**.
* Each subject is part of the key when publishing data to zenoh, refer to the section about [keys](#11-pubsub-messaging), this helps the sender and receiver to put the information into a **context**.

##### 2.1.1 Naming convention for `subject`s

TODO

#### 2.2 Req/Rep messaging (RPC)

Zenoh supports a generalized version of Remote Procedure Calls, namely [queryables](https://zenoh.io/docs/manual/abstractions/#queryable). This is leveraged for Request/Response messaging (RPC) in keelson with the following decrees:

* All RPC endpoints (queryables) should be declared "complete"
* All RPC endpoints (queryables) should be defined by a protobuf service definition
* All RPC endpoints (queryables) should make use of the common `ErrorResponse` message type and the `reply_err` functionality in zenoh to propagate errors from callee to caller.

A set of generic interface definitions can be found in [interfaces/](./interfaces/).

## How to use

`keelson`is but a small set of rules on top of zenoh. Make sure to first be aquainted with zenoh and then ensure your application adheres to the key-space design and message format advocated and supported by `keelson`, either through the useage of one of the available SDKs or just by compliance.

See details of the SDKs for usage examples in the respective languages.

## For developers

There is a devcontainer setup for the repository which is suitable for the whole monorepo. Use it!

### To make a new release

Make sure to do the following:
* Update version numbers in the respective SDKs
* Make a new release on Github with name according to version number

