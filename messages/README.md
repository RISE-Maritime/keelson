# Message defections  

In the folder messages are the defections for subjects and procedures

Messages are divided into the following packages:

- Primitives (Simple and flexible for of messages)
- Complex (Combined messages or specific purpose messages()
- Foxglove ([Foxglove formatdefintion](https://github.com/foxglove/schemas/tree/main/schemas/proto/foxglove)) 

## Add or Edit subjects payloads

Each message is wrapped in an [Envelope](./Envelope.proto) with include the payload message and all the payloads are located in the [payloads folder](./payloads/). Each payload is an protobuf definition as the file type .proto


Foxglove messages should be avoided to modify and if really 

### To add

1) Just create a new proto file and name it descriptively in CamelCase.
2) 