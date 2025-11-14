# Keelson Node-RED Nodes

This package provides Node-RED nodes for working with Keelson messages over MQTT using the [zenoh-plugin-mqtt](https://github.com/eclipse-zenoh/zenoh-plugin-mqtt) bridge.

## Installation

Install the Keelson JavaScript SDK in your Node-RED user directory:

```bash
cd ~/.node-red
npm install keelson-js
```

Restart Node-RED, and the Keelson nodes will appear in the palette under the "keelson" category.

## Available Nodes

### keelson-enclose

Wraps payload bytes in a Keelson Envelope protobuf message with a timestamp.

**Input:**
- `msg.payload` (Buffer | Uint8Array): The payload bytes to enclose
- `msg.enclosed_at` (Date | string | number, optional): Timestamp for when the data was enclosed. If not provided, uses current time.

**Output:**
- `msg.payload` (Buffer): The serialized Keelson envelope, ready for MQTT publish

**Usage:**
Connect this node before an MQTT publish node to wrap your data in a Keelson envelope.

### keelson-uncover

Extracts payload bytes and timestamps from a Keelson Envelope protobuf message.

**Input:**
- `msg.payload` (Buffer | Uint8Array): The serialized Keelson envelope (from MQTT subscribe)

**Output:**
- `msg.payload` (Buffer): The extracted payload bytes
- `msg.enclosed_at` (Date | undefined): When the data was originally enclosed
- `msg.uncovered_at` (Date): When the envelope was uncovered (current time)

**Usage:**
Connect this node after an MQTT subscribe node to extract the payload from a Keelson envelope.

### keelson-pack

Encodes a JavaScript object to protobuf bytes using a Keelson subject.

**Configuration:**
- `Subject`: The Keelson subject to use for encoding (e.g., "location_fix"). Leave empty to extract from `msg.topic`.

**Input:**
- `msg.payload` (object): The JavaScript object to encode
- `msg.topic` (string, optional): Keelson key to extract subject from (if subject not configured)

**Output:**
- `msg.payload` (Buffer): The protobuf-encoded bytes
- `msg.keelson_subject` (string): The subject used for encoding
- `msg.keelson_type` (string): The protobuf type name used

**Usage:**
Use this to convert JavaScript objects to protobuf format before enclosing them in an envelope.

### keelson-unpack

Decodes protobuf bytes to a JavaScript object using a Keelson subject.

**Configuration:**
- `Subject`: The Keelson subject to use for decoding (e.g., "location_fix"). Leave empty to extract from `msg.topic`.

**Input:**
- `msg.payload` (Buffer | Uint8Array): The protobuf-encoded bytes
- `msg.topic` (string, optional): Keelson key to extract subject from (if subject not configured)

**Output:**
- `msg.payload` (object): The decoded JavaScript object
- `msg.keelson_subject` (string): The subject used for decoding
- `msg.keelson_type` (string): The protobuf type name used

**Usage:**
Use this after uncovering an envelope to decode the payload bytes to a JavaScript object.

## Example Flows

### Publishing a Keelson Message

```
[Inject Node] --> [Function Node] --> [keelson-pack] --> [keelson-enclose] --> [MQTT Out]
```

The Function node creates a JavaScript object:
```javascript
msg.payload = {
    latitude: 57.7089,
    longitude: 11.9746,
    altitude: 10.5
};
msg.topic = "vessel/123/location";
return msg;
```

Configure the `keelson-pack` node with subject "location_fix", then the `keelson-enclose` node wraps it in an envelope, and finally MQTT Out publishes it.

### Subscribing to a Keelson Message

```
[MQTT In] --> [keelson-uncover] --> [keelson-unpack] --> [Debug Node]
```

MQTT In subscribes to a topic, `keelson-uncover` extracts the payload and timestamps, `keelson-unpack` decodes the protobuf to a JavaScript object, and Debug displays it.

### Full Round-Trip Example

**Publisher Flow:**
```
[Inject] --> [Function: Create Data] --> [keelson-pack: "location_fix"] --> [keelson-enclose] --> [MQTT Out: "vessel/@v0/123/pubsub/location_fix/gps"]
```

**Subscriber Flow:**
```
[MQTT In: "vessel/@v0/+/pubsub/location_fix/#"] --> [keelson-uncover] --> [keelson-unpack] --> [Debug]
```

The `keelson-unpack` node can extract the subject from the MQTT topic automatically, so you don't need to configure it explicitly.

## Subject Extraction from Topics

The `keelson-pack` and `keelson-unpack` nodes can automatically extract the subject from `msg.topic` if it follows the Keelson pubsub key format:

```
{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}
```

For example, from the topic `vessel/@v0/123/pubsub/location_fix/gps`, the subject "location_fix" will be extracted.

## Well-Known Subjects

The Keelson SDK includes 180+ well-known subjects for common maritime data types:

- **Raw Data**: `raw`, `raw_json`, `raw_nmea0183`
- **Positioning**: `location_fix`, `heading_true_north_deg`, `course_over_ground_deg`
- **Vessel Motion**: `roll_deg`, `pitch_deg`, `yaw_deg`
- **Propulsion**: `engine_throttle_pct`, `propeller_rate_rpm`, `rudder_angle_deg`
- **Environmental**: `air_temperature_celsius`, `water_temperature_celsius`, `true_wind_speed_mps`
- **Media**: `image_raw`, `image_compressed`, `video_compressed`, `laser_scan`, `point_cloud`
- And many more...

See the [subjects.yaml](https://github.com/RISE-Maritime/keelson/blob/main/messages/subjects.yaml) file for the complete list.

## Passthrough Behavior

All Keelson nodes pass through message properties that are not used by the node. This allows you to chain nodes together and preserve important metadata like timestamps, entity IDs, and custom properties throughout your flow.

## MQTT Connection

These nodes are designed to work with the built-in MQTT nodes in Node-RED. For connecting to a Zenoh network, use the [zenoh-plugin-mqtt](https://github.com/eclipse-zenoh/zenoh-plugin-mqtt) bridge to translate between MQTT and Zenoh protocols.

## Naming Conventions

The nodes follow the same naming conventions as the Keelson JavaScript SDK and Python reference implementation:

- Functions use `snake_case` naming
- Subjects use `snake_case` naming
- Arguments and return values match the SDK functions:
  - `enclose(payload, enclosed_at?)` → `Envelope`
  - `uncover(encodedEnvelope)` → `[uncovered_at, enclosed_at, payload]`
  - `encodePayloadFromTypeName(typeName, payload)` → `Uint8Array`
  - `decodePayloadFromTypeName(typeName, bytes)` → `object`

## License

Apache-2.0
