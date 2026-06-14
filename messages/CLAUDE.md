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

## qos.yaml

Companion to `subjects.yaml`: it assigns a **QoS profile** to each subject —
how it should travel (priority / congestion / reliability / express), as
opposed to `subjects.yaml` which says *what* it carries. It is also a
source-of-truth file, copied verbatim into both SDKs by the generate scripts
(`keelson/qos.yaml`, `keelson/qos.json`).

Structure: a `profiles:` block (named, zenoh-shaped tuples), a `default:`
profile, and a sparse `subjects:` map — **only** subjects whose class differs
from the default are listed; everything else inherits the `default` profile.
The values are deliberately zenoh-flavoured strings; the SDK maps them to
`zenoh.*` enums in `keelson.scaffolding.qos_zenoh`. Consumed in Python via
`keelson.qos.qos_for(subject)` and in connectors via
`keelson.scaffolding.declare_publisher(session, key)` (derives the profile from
the subject in the key). `test_qos.py` enforces that every assignment names a
real subject and a defined profile.

**Changing QoS:** edit `messages/qos.yaml` (add/retune a profile, or assign a
subject to one), then regenerate both SDKs — no connector code changes, since
connectors re-derive QoS from the subject at runtime.

## Adding a New Subject

**First decide whether it should be a subject at all, and in what shape.** See [protocol-specification.md §2.2.1 "What belongs on the bus"](../docs/protocol-specification.md): observations become measurement subjects (separable scalars split one-per-subject; an indivisible frame like a point cloud stays whole); data derivable from another subject by a fixed mapping (Beaufort from wind speed, a visibility band from range) does **not** go on the bus; externally-authored bundles (alerts, forecasts) are quarantined types carrying provenance. Then:

1. Add the entry to `subjects.yaml` (alphabetical within its section)
2. If the type doesn't exist yet, create a new `.proto` in `payloads/` (or `payloads/foxglove/`)
3. If its QoS stance differs from the `default` profile, assign it in `qos.yaml` (otherwise nothing to do — it inherits `default`)
4. Regenerate Python SDK: `cd sdks/python && ./generate_python.sh`
5. Regenerate JavaScript SDK: `cd sdks/js && ./generate_javascript.sh`
6. Regenerate docs: `./generate_docs.sh`
7. Run tests: `uv run pytest sdks/python/tests/`

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
