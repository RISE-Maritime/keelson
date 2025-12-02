# Keelson JavaScript SDK

JavaScript SDK for [Keelson](https://github.com/RISE-Maritime/keelson): a modern, open protocol for maritime and sensor data. This package provides all the tools needed to encode, decode, and manage Keelson messages and envelopes in JavaScript/TypeScript environments.

---

## Installation

```bash
npm install @rise-maritime/keelson-js
```

---

## SDK Usage

### Envelope Management

```js
import { enclose, uncover } from 'keelson-js';

// Wrap payload in envelope
const envelope = enclose(payloadBytes, Date.now());

// Unwrap envelope
const [uncoveredAt, enclosedAt, payload] = uncover(envelope);
```

### Payload Encoding/Decoding

```js
import { encodePayloadFromTypeName, decodePayloadFromTypeName } from 'keelson-js';

// Encode JS object to protobuf
const bytes = encodePayloadFromTypeName('location_fix', jsObject);

// Decode protobuf to JS object
const obj = decodePayloadFromTypeName('location_fix', bytes);
```

---

## Well-Known Subjects

Keelson includes 180+ well-known subjects for maritime data types, e.g.:

- `location_fix`, `heading_true_north_deg`, `roll_deg`, `engine_throttle_pct`, `image_raw`, `video_compressed`, ...

See [`messages/subjects.yaml`](https://github.com/RISE-Maritime/keelson/blob/main/messages/subjects.yaml) for the full list.

---

## Naming Conventions

- Functions: `snake_case`
- Subjects: `snake_case`
- Arguments/returns match SDK:
  - `enclose(payload, enclosed_at?)` → `Envelope`
  - `uncover(encodedEnvelope)` → `[uncovered_at, enclosed_at, payload]`
  - `encodePayloadFromTypeName(typeName, payload)` → `Uint8Array`
  - `decodePayloadFromTypeName(typeName, bytes)` → `object`

---

## Development Setup

1. Generate protobuf messages:
	```bash
	chmod +x generate_javascript.sh
	./generate_javascript.sh
	```
2. Run tests:
	```bash
	npm install --save-dev jest @types/jest ts-jest
	npx jest
	```

---

## Node-RED Nodes

This package also provides Node-RED nodes for seamless integration with MQTT and Zenoh. To use in Node-RED:

```bash
cd ~/.node-red
npm install @rise-maritime/keelson-js
```
Restart Node-RED. The Keelson nodes will appear in the palette under the "keelson" category.

### Available Nodes

- **keelson-enclose**: Wraps payload bytes in a Keelson Envelope protobuf message with a timestamp.
- **keelson-uncover**: Extracts payload bytes and timestamps from a Keelson Envelope protobuf message.
- **keelson-encode-payload**: Encodes a JavaScript object to protobuf bytes using a Keelson subject.
- **keelson-decode-payload**: Decodes protobuf bytes to a JavaScript object using a Keelson subject.

#### Node-RED Example Flows

**Publishing a Keelson Message:**
```
[Inject] --> [Function: Create Data] --> [keelson-encode-payload] --> [keelson-enclose] --> [MQTT Out or zenoh-put]
```
Function node example:
```js
msg.payload = {
	 latitude: 57.7089,
	 longitude: 11.9746,
	 altitude: 10.5
};
msg.topic = "vessel/@v0/123/pubsub/location_fix/gps";
return msg;
```

**Subscribing to a Keelson Message:**
```
[MQTT In or zenoh-subscribe] --> [keelson-uncover] --> [keelson-decode-payload] --> [Debug]
```


#### Connectivity: Zenoh (Recommended) & MQTT

- **Zenoh (Recommended):** Use [`@freol35241/nodered-contrib-zenoh`](https://github.com/freol35241/nodered-contrib-zenoh) for direct, native Zenoh protocol support. Buffers are handled natively by Zenoh nodes, and you get access to Zenoh features like queries and attachments without needing a bridge.
- **MQTT (Alternative):** You can also use Node-RED MQTT nodes with [zenoh-plugin-mqtt](https://github.com/eclipse-zenoh/zenoh-plugin-mqtt) as a bridge to Zenoh networks. Buffers are handled natively by MQTT nodes.

#### Subject Extraction from Topics

`keelson-encode-payload` and `keelson-decode-payload` can extract the subject from `msg.topic` if it matches:
```
{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}
```
E.g., from `vessel/@v0/123/pubsub/location_fix/gps`, the subject is `location_fix`.

#### Passthrough Behavior

All Keelson nodes pass through unused message properties, preserving metadata like timestamps, entity IDs, and custom fields.

---

## License

Apache-2.0
