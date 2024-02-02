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

## Pub/Sub key-space

### Design philosophy
We tred to adhere to the following key aspects when defining the key-space design used in keelson:

* Low per-message overhead
* Transparancy with regards to the data and the structure
* Simplicity is nice, but complexity is sometimes necessary

### Design in practice

All messages conveyed on a keelson databus should be protobuf-encoded keelson `envelope`s containing `payload`s.

Keelson support a set of (centrally handled) well-known payloads:
* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a tag that describes how to interpret the **information**.
* Each tag is part of a key expression that helps the sender and receiver to put the information into a **context**.

Keelson also support unknown payload types, with the condition that these do NOT use an existing `tag`. Payloads of unknown types (tags) will be treaded opaquely as binary data.

The pub/sub specific lower level hierarchy in the key space therefore consists of the following levels:

  `.../{tag}/{source_id}`

With
  * `tag` see [tags.yaml](./messages/tags.yaml)
  * `source_id` being a unique id for the source producing/consuming the information described by `tag`. `source_id` may contain any number of addititional topic levels (i.e. forward slashes `/`)

For example:

  `keelson/vessel_1/rudder_angle_deg/starboard`

  `keelson/sjfv/ais_msg_123/{mmsi}`

  `keelson/moc/lever_position_pct/arduino/right/channel/0`


### Req/Rep key-space (RPC)

The req/rep specific lower level hierarchy in the topic space consists of the following levels:

  `.../{responder_id}/{procedure}`

With:
  * `responder_id` being a unique id for the responder that provides the remote procedure. `responder_id` may contain any number of addititional topic levels (i.e. forward slashes `/`)
  * `procedure` being a descriptive name of the procedure

For example:

  `keelson/vessel_1/autopilot/set_target_heading`

Note that keelson does **NOT** enforce any requirements with regards to payloads/schemas on Req/Rep traffic.
