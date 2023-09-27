# keel
Key Enabling Ecosystem Link

**NOTE**: Work in progress...

`keel` is a flexible, fast and resource-friendly communication backbone enabling edge-to-edge, machine-to-machine communication. It leverages [zenoh](https://github.com/eclipse-zenoh/zenoh) for message based communication and [mediamtx](https://github.com/bluenviron/mediamtx) for streaming of audio/video.

**TODO**: Image/sketch

Design philosophy:
* Well-known payloads are defined by a schema that describes how to interpret the **data**.
* Each (well-known) payload is associated with a tag that describes how to interpret the **information**.
* Each tag is part of a key expression that helps the sender and receiver to put the information into a **context**.

Design philosophy-as-implemented:
* Key expressions should adhere to the following template: 

  `keel/{ENTITY_ID}/{INTERFACE_TYPE}/{INTERFACE_ID}/{BREFV_TAG}/{SOURCE_ID}`

  With:

    * `ENTITY_ID` being a unique id representing an entity within the network
    * `INTERFACE_TYPE` being the type of interface that is interacted with towards keel
    * `INTERFACE_ID` being a unique id for this type of interface within the encompassing entity
    * `BREFV_TAG` see [tags.yaml](./brefv/tags.yaml)
    * `SOURCE_ID` being a unique id for the source producing/consuming the information described by `BREFV_TAG`

  For example:

  `keel/vessel_1/n2k/yden-02_1/rudder_angle_deg/starboard`

  `keel/ext/netline/sjfv/ais_msg_123/{mmsi}`

  `keel/moc/haddock/sealog-8/steering_angle_pct/right`

* Messages should be protobuf-encoded brefv `envelope`s containing `payload`s.
* Payloads should be either:
    * A well-known payload type as associated with an existing `BREFV_TAG` (see [tags.yaml](./brefv/tags.yaml)) and defined in [brefv/payloads](./brefv/payloads)
    * An unknown payload type NOT using an existing `BREFV_TAG`


## Repository structure
The core parts of `keel` are maintained and developed inside a monorepo (this repo) to ensure consistency and interoperability within versions during early development. At some point in the future, this monorepo may (or may not) be splitted into separate repositories.

Parts:

* [**Brefv**](./brefv/README.md) is the message encoding protocol in use by keel
* [**Hold**](./hold/README.md)  is the default recording and replaying functionality in keel
* [**Infrastructure guidelines**](./infrastructure/README.md) contains bits and pieces to set up a working zenoh network infrastructure.

Versions:

| Keel version | Zenoh version |
|--------------|---------------|
| 0.1.0        | 0.7.2-rc      |

