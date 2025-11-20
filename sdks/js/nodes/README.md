# Keelson Node-RED Nodes

This package provides Node-RED nodes for working with Keelson messages. The nodes handle message encoding/decoding and envelope management, and can be used with MQTT or Zenoh for connectivity (see [Connectivity](#connectivity) section).

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
- `msg.payload` (Buffer): The serialized Keelson envelope

**Usage:**
Wraps binary payload data in a Keelson envelope before transmission.

### keelson-uncover

Extracts payload bytes and timestamps from a Keelson Envelope protobuf message.

**Input:**
- `msg.payload` (Buffer | Uint8Array): The serialized Keelson envelope

**Output:**
- `msg.payload` (Buffer): The extracted payload bytes
- `msg.enclosed_at` (Date | undefined): When the data was originally enclosed
- `msg.uncovered_at` (Date): When the envelope was uncovered (current time)

**Usage:**
Extracts the payload and timestamps from a received Keelson envelope.

### keelson-encode-payload

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
Converts JavaScript objects to protobuf binary format before enclosing them in an envelope.

### keelson-decode-payload

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
Converts protobuf binary data to JavaScript objects after uncovering an envelope.

## Connectivity

The Keelson nodes use Node.js **Buffer** objects as the standard interface for binary data. This makes them compatible with various transport mechanisms. You have two main options for connectivity:

### Option 1: MQTT (via zenoh-plugin-mqtt)

Use the built-in Node-RED MQTT nodes with the [zenoh-plugin-mqtt](https://github.com/eclipse-zenoh/zenoh-plugin-mqtt) bridge to connect to a Zenoh network.

**Buffer Interface**: MQTT nodes work seamlessly with Buffer objects:
- **Publishing**: MQTT Out accepts `msg.payload` as Buffer
- **Subscribing**: MQTT In outputs `msg.payload` as Buffer

**Example Flow:**
```
[keelson-encode-payload] → [keelson-enclose] → [MQTT Out]
[MQTT In] → [keelson-uncover] → [keelson-decode-payload]
```

**Setup:**
1. Install and configure zenoh-plugin-mqtt on your Zenoh router
2. Use standard Node-RED MQTT nodes
3. Connect to the MQTT bridge endpoint

### Option 2: Zenoh (via nodered-contrib-zenoh)

Use [`@freol35241/nodered-contrib-zenoh`](https://github.com/freol35241/nodered-contrib-zenoh) for direct Zenoh integration without MQTT.

**Buffer Interface**: Zenoh nodes are fully compatible with Buffer objects:
- **Publishing** (`zenoh-put`): Accepts `msg.payload` as Buffer (and converts other types to Buffer automatically)
- **Subscribing** (`zenoh-subscribe`): Outputs `msg.payload` as Buffer

**Example Flow:**
```
[keelson-encode-payload] → [keelson-enclose] → [zenoh-put]
[zenoh-subscribe] → [keelson-uncover] → [keelson-decode-payload]
```

**Setup:**
1. Install: `npm install @freol35241/nodered-contrib-zenoh`
2. Configure a Zenoh session node pointing to your Zenoh router
3. Use zenoh-put and zenoh-subscribe nodes directly

**Advantages**:
- Direct Zenoh protocol support
- No MQTT bridge needed
- Native Zenoh features (queries, queryables, attachments)

### Buffer Compatibility

All Keelson nodes use Node.js **Buffer** for binary data, which ensures compatibility with both connectivity options:

**Input Handling:**
- Keelson nodes accept both `Buffer` and `Uint8Array`
- Automatically convert between types as needed
- No manual conversion required in your flows

**Output Format:**
- All Keelson nodes output `msg.payload` as `Buffer`
- Compatible with MQTT nodes (expect Buffer)
- Compatible with Zenoh nodes (accept Buffer)

**Example - No Conversion Needed:**
```javascript
// This works seamlessly:
[zenoh-subscribe]        // outputs Buffer
  → [keelson-uncover]    // accepts Buffer, outputs Buffer
  → [keelson-decode-payload]  // accepts Buffer, outputs Object
  → [keelson-encode-payload]  // accepts Object, outputs Buffer
  → [keelson-enclose]    // accepts Buffer, outputs Buffer
  → [zenoh-put]          // accepts Buffer
```

## Example Flows

### Publishing a Keelson Message

```
[Inject Node] --> [Function Node] --> [keelson-encode-payload] --> [keelson-enclose] --> [Transport]
```

The Function node creates a JavaScript object:
```javascript
msg.payload = {
    latitude: 57.7089,
    longitude: 11.9746,
    altitude: 10.5
};
msg.topic = "vessel/@v0/123/pubsub/location_fix/gps";
return msg;
```

Configure the `keelson-encode-payload` node with subject "location_fix", then `keelson-enclose` wraps it in an envelope. The Transport node can be either MQTT Out or zenoh-put.

### Subscribing to a Keelson Message

```
[Transport] --> [keelson-uncover] --> [keelson-decode-payload] --> [Debug Node]
```

The Transport node (MQTT In or zenoh-subscribe) receives the message, `keelson-uncover` extracts the payload and timestamps, `keelson-decode-payload` decodes the protobuf to a JavaScript object, and Debug displays it.

### Full Round-Trip Example

**Publisher Flow:**
```
[Inject] --> [Function: Create Data] --> [keelson-encode-payload: "location_fix"] --> [keelson-enclose] --> [Transport: "vessel/@v0/123/pubsub/location_fix/gps"]
```

**Subscriber Flow:**
```
[Transport: "vessel/@v0/+/pubsub/location_fix/#"] --> [keelson-uncover] --> [keelson-decode-payload] --> [Debug]
```

The `keelson-decode-payload` node can extract the subject from the topic automatically, so you don't need to configure it explicitly.

## Subject Extraction from Topics

The `keelson-encode-payload` and `keelson-decode-payload` nodes can automatically extract the subject from `msg.topic` if it follows the Keelson pubsub key format:

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
