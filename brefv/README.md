# brefv

`brefv` is the specification of the messaging protocol in use by keelson. Since keelson is based on [Zenoh](https://zenoh.io/) the specifics of `brefv` assumes `Zenoh` as the communication backbone.

Some initial clarifications:

1. `Zenoh` supports both pub/sub and req/rep messaging patterns, they all live in the same key expression "space". This is important and requires careful consideration of the design of the key expression hierarchy.
2. `brefv` uses the name `topic` and `key expression` interchangebly. 
3. Without exceptions, topics should adhere to `snake_case` style.

## Design of topic "space"

The shared topic space for pub/sub and req/rep messaging has a common base hierarchy consiting of 4 levels:

`{realm}/{entity_id}/{interface_type}/{interface_id}`

  With:
    * `realm` being a unique id for a domain/realm
    * `entity_id` being a unique id representing an entity within the realm
    * `interface_type` being the type of interface that is interacted with towards keelson
    * `interface_id` being a unique id for this type of interface within the encompassing entity

### Pub/Sub specifics

Design philosophy:
* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a tag that describes how to interpret the **information**.
* Each tag is part of a key expression that helps the sender and receiver to put the information into a **context**.

The pub/sub specific lower level hierarchy in the topic space consists of the following levels:

  `.../{tag}/{source_id}`

With
  * `tag` see [tags.yaml](./tags.yaml)
  * `source_id` being a unique id for the source producing/consuming the information described by `tag`. `source_id` may contain any number of addititional topic levels (i.e. forward slashes `/`)

For example:

  `keelson/vessel_1/n2k/yden-02_1/rudder_angle_deg/starboard`

  `keelson/ext/netline/sjfv/ais_msg_123/{mmsi}`

  `keelson/moc/haddock/sealog-8/lever_position_pct/arduino/right/channel/0`

* Messages should be protobuf-encoded brefv `envelope`s containing `payload`s.
* Payloads should be either:
  * A well-known payload type as associated with an existing `tag` (see [tags.yaml](./tags.yaml)) and defined in [payloads](./payloads)
  * An unknown payload type NOT using an existing `tag`


### Req/Rep specifics

The req/rep specific lower level hierarchy in the topic space consists of the following levels:

  `.../rpc/{procedure}`

With:
  * `rpc` being the static word `rpc` to highlight that this is indeed an endpoint for remote procedure calls
  * `procedure` being a descriptive name of the procedure

For example:

  `keelson/vessel_1/n2k/yden-02_1/rpc/set_target_heading`


## Libraries

This repo hosts several helper libraries in different programming languages:
* [Python](./python/README.md)
* [JavaScript](./js/README.md) (WIP)

These libraries typically contain helping functionality for working with topics, tags and payloads.