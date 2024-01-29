# Key-space design

Some initial clarifications:

1. `Zenoh` supports both pub/sub and req/rep messaging patterns, they all live in the same key expression "space". This is important and requires careful consideration of the design of the key expression hierarchy.
2. `keelson` uses the name `topic` and `key expression` interchangebly. 
3. Without exceptions, key expressions should adhere to `snake_case` style.

The shared key-space for pub/sub and req/rep messaging in keelson has a common base hierarchy consiting of 4 levels:

`{realm}/{entity_id}`

  With:
    * `realm` being a unique id for a domain/realm
    * `entity_id` being a unique id representing an entity within the realm

### Pub/Sub specifics

Design philosophy:
* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a tag that describes how to interpret the **information**.
* Each tag is part of a key expression that helps the sender and receiver to put the information into a **context**.

The pub/sub specific lower level hierarchy in the topic space consists of the following levels:

  `.../{tag}/{source_id}`

With
  * `tag` see [tags.yaml](./messages/tags.yaml)
  * `source_id` being a unique id for the source producing/consuming the information described by `tag`. `source_id` may contain any number of addititional topic levels (i.e. forward slashes `/`)

For example:

  `keelson/vessel_1/rudder_angle_deg/starboard`

  `keelson/sjfv/ais_msg_123/{mmsi}`

  `keelson/moc/lever_position_pct/arduino/right/channel/0`

* Messages should be protobuf-encoded keelson `envelope`s containing `payload`s.
* Payloads should be either:
  * A well-known payload type as associated with an existing `tag` (see [tags.yaml](./messages/tags.yaml)) and defined in [payloads](./messages/payloads)
  * An unknown payload type NOT using an existing `tag`


### Req/Rep specifics

The req/rep specific lower level hierarchy in the topic space consists of the following levels:

  `.../{responder_id}/{procedure}`

With:
  * `responder_id` being a unique id for the responder that provides the remote procedure. `responder_id` may contain any number of addititional topic levels (i.e. forward slashes `/`)
  * `procedure` being a descriptive name of the procedure

For example:

  `keelson/vessel_1/autopilot/set_target_heading`

Note that keelson does **NOT** enforce any requirements with regards to payloads/schemas on Req/Rep traffic.
