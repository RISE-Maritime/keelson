# brefv

Design philosophy:
* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a tag that describes how to interpret the **information**.
* Each tag is part of a key expression that helps the sender and receiver to put the information into a **context**.

Design philosophy-as-implemented:
* Key expressions (topics) should adhere to the following template: 

  `{realm}/{entity_id}/{interface_type}/{interface_id}/{tag}/{source_id}`

  With:
    * `realm` being a unique id for a domain/realm
    * `entity_id` being a unique id representing an entity within the realm
    * `interface_type` being the type of interface that is interacted with towards keelson
    * `interface_id` being a unique id for this type of interface within the encompassing entity
    * `tag` see [tags.yaml](./tags.yaml)
    * `source_id` being a unique id for the source producing/consuming the information described by 

  For example:

  `keelson/vessel_1/n2k/yden-02_1/rudder_angle_deg/starboard`

  `keelson/ext/netline/sjfv/ais_msg_123/{mmsi}`

  `keelson/moc/haddock/sealog-8/steering_angle_pct/right`

* Messages should be protobuf-encoded brefv `envelope`s containing `payload`s.
* Payloads should be either:
    * A well-known payload type as associated with an existing `tag` (see [tags.yaml](./tags.yaml)) and defined in [payloads](./payloads)
    * An unknown payload type NOT using an existing `tag`


Comes with support for:
* [Python](./python/README.md)
* [JavaScript](./js/README.md) (WIP)