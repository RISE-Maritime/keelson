# Messages

Protobuf definitions and the subject registry. This is the source of truth for all generated SDK code.

## subjects.yaml

Maps well-known subject names to protobuf message types. Format: `subject_name: package.MessageType`

```yaml
raw:                keelson.TimestampedBytes
raw_nmea0183:       keelson.TimestampedString
location_fix:       foxglove.LocationFix
compressed_image:   foxglove.CompressedImage
```

There are 180+ subjects. Every subject must reference a type defined in `payloads/`.

## Adding a New Subject

1. Add the entry to `subjects.yaml` (alphabetical within its section)
2. If the type doesn't exist yet, create a new `.proto` in `payloads/` (or `payloads/foxglove/`)
3. Regenerate Python SDK: `cd sdks/python && ./generate_python.sh`
4. Regenerate JavaScript SDK: `cd sdks/js && ./generate_javascript.sh`
5. Regenerate docs: `./generate_docs.sh`
6. Run tests: `uv run pytest sdks/python/tests/`

## Envelope.proto

Core wrapper message in package `core`:

```protobuf
message Envelope {
    google.protobuf.Timestamp enclosed_at = 1;
    bytes payload = 2;
}

message KeyEnvelopePair {
    google.protobuf.Timestamp timestamp = 1;
    string key = 2;
    bytes envelope = 3;
}
```

## payloads/ Directory

- **Keelson types** (package `keelson`): `Primitives.proto` (TimestampedFloat, TimestampedString, TimestampedBytes, TimestampedInt, etc.), `Alarm.proto`, `Audio.proto`, `NetworkStatus.proto`, `SensorStatus.proto`, `RadarReading.proto`, etc.
- **Foxglove types** (package `foxglove`, in `payloads/foxglove/`): Standard visualization types — `LocationFix.proto`, `CompressedImage.proto`, `PointCloud.proto`, `LaserScan.proto`, `FrameTransform.proto`, `Log.proto`, etc.

## Proto Conventions

- Keelson types use `package keelson;`
- Foxglove types use `package foxglove;` and live in `payloads/foxglove/`
- Timestamp fields use `google.protobuf.Timestamp`
- Imports within foxglove reference sibling files: `import "foxglove/Point3.proto";`

## interfaces/

RPC interface definitions live at the **repo root** (`/interfaces/`), not under `messages/`. Contains: `Configurable.proto`, `ErrorResponse.proto`, `NetworkPingPong.proto`, `Subscriber.proto`, `WHEPProxy.proto`.
