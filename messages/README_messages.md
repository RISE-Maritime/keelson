# Message defections  

In the folder messages are the defections for subjects and procedures

## Messages are divided into the following packages:

- Primitives (Simple and flexible for of messages)
- Topic specific Package (Combined messages or specific purpose messages)

## Third party message definition integrated to Keelson

- Foxglove ([Foxglove formatdefintion](https://github.com/foxglove/schemas/tree/main/schemas/proto/foxglove))

## Message Core ENVELOPE

Each message is wrapped in an [Envelope](./Envelope.proto) with include the payload message and all the payloads are located in the [payloads folder](./payloads/). Each payload is an protobuf definition as the file type .proto

TODO: Should envelop continue to exist or be moved to 

### To add

1) Just create a new proto file and name it descriptively in CamelCase.



### Design philosophy

#### Naming variables  

The naming describes the type of measurement and adheres to the format <property>_<unit>, where:

- property: Describes the property measured in the entity.
- unit: Describes the units of the measurement.

For example, the following tag markers:
speed_rpm
power_kw

#### Packages are by entity 

#### Messages structure 

- Messages are built up by common/Primitives  

## Tool for visualizing protobuf files

[https://protodot.seamia.net/](https://protodot.seamia.net/)
