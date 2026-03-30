# platform

Contains helper binaries for providing (semi-)static data of a platform (such as a vessel) for keelson services.

## `platform-geometry`

```
usage: platform-geometry [-h] [--log-level LOG_LEVEL] [--connect CONNECT] -r REALM -e ENTITY_ID -s SOURCE_ID --config CONFIG [--interval INTERVAL]

Command line utility tool for outputting geometrical information about a platform on a given interval

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --connect CONNECT     Endpoints to connect to. (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --config CONFIG       A path to a JSON-encoded configuration file for this platform. (default: None)
  --interval INTERVAL   Interval (second) at which the information will be put to zenoh. (default: 10)
```

An example of the configuration file is provided in [`example-config.json`](./example-config.json).

### Example startup

From inside `connectors/platform/`:

```bash
python bin/platform-geometry2keelson.py \
  --realm rise \
  --entity-id vessel-example \
  --source-id platform \
  --config example-config.json \
  --interval 10
```

Or with `uv` from the repo root:

```bash
uv run connectors/platform/bin/platform-geometry2keelson.py \
  --realm rise \
  --entity-id vessel-example \
  --source-id platform \
  --config connectors/platform/example-config.json \
  --interval 10
```

To connect to a remote Zenoh router:

```bash
uv run connectors/platform/bin/platform-geometry2keelson.py \
  --realm rise \
  --entity-id vessel-example \
  --source-id platform \
  --config connectors/platform/example-config.json \
  --connect tcp/192.168.1.100:7447
```

### Configuration Schema

The configuration file must conform to the JSON schema defined in [`config-schema.json`](./config-schema.json).

## Platform / Local Coordinate system

The local coordinate system follows standard maritime and naval architecture conventions, which differ from some common open-source, ISO standard or robotics systems (such as ROS or ISO 8855). This choice ensures consistency with established shipbuilding and marine engineering practices.

**Translation** (in meters):

- X - positive forward
- Y - positive to starboard (right)
- Z - positive down

**Rotation** (yaw, pitch, roll in degrees):

- Applied in the following order: **yaw → pitch → roll**

![alt text](./Ship.png)

### Schema Structure

```json
{
  platform_type: string (optional)    - One of: "vessel", "landkrabba", "roc"
  description: string (optional)      - Human-readable description of the platform
  name: string (optional)
  length_over_all_m: number (optional)
  breadth_over_all_m: number (optional)
  mmsi_number: integer (optional)     - MMSI for AIS identification
  imo_number: integer (optional)      - IMO number (0 if not assigned)
  call_sign: string (optional)        - Radio call sign

  operational_limits: object (optional)
    └─ Operational envelope of this platform
       ├─ max_speed_knots: number
       ├─ max_wind_speed_mps: number
       ├─ max_wave_height_m: number
       ├─ max_range_m: number
       └─ endurance_hours: number

  ccrp_m: object (optional)
    └─ Consistent Common Reference Point in the platform local coordinate system
       ├─ x: number (forward, meters)
       ├─ y: number (starboard, meters)
       └─ z: number (down, meters)

  frame_transforms: array (optional)
    └─ Array of coordinate frame transformations
       └─ [
            {
              parent_frame_id: string (required)
              child_frame_id: string (required)
              sensor_type: string (optional)    - One of: "camera", "lidar", "radar", "gnss", "imu", "other"
              sensor_description: string (optional) - Human-readable label for the sensor

              translation_m: object (required)
                └─ Translation in meters from parent to child frame
                   ├─ x: number (X-axis translation in meters)
                   ├─ y: number (Y-axis translation in meters)
                   └─ z: number (Z-axis translation in meters)

              rotation_deg: object (required)
                └─ Rotation in degrees from parent to child frame
                   └─ Order of operation: YAW → PITCH → ROLL
                      ├─ yaw: number (-180 to 180 degrees)
                      ├─ pitch: number (-180 to 180 degrees)
                      └─ roll: number (-180 to 180 degrees)
            }
          ]
}
```

## Queryable Configuration Interface

The connector exposes a live configuration interface via Zenoh RPC, following the standard Keelson `Configurable` interface:

| Procedure | Key pattern | Description |
|---|---|---|
| `get_config` | `{realm}/@v0/{entity_id}/@rpc/get_config/{source_id}` | Returns the full current configuration as JSON |
| `set_config` | `{realm}/@v0/{entity_id}/@rpc/set_config/{source_id}` | Replaces the running configuration (validated against the schema) |

Configuration changes applied via `set_config` take effect on the next publish interval. The updated config is also published to the `configuration_json` subject.

### Published Subjects

| Subject | Type | Description |
|---|---|---|
| `length_over_all_m` | `TimestampedFloat` | Length over all in meters |
| `breadth_over_all_m` | `TimestampedFloat` | Breadth over all in meters |
| `frame_transform` | `foxglove.FrameTransform` | One message per configured frame transform |
| `mmsi_number` | `TimestampedInt` | MMSI number (if configured) |
| `imo_number` | `TimestampedInt` | IMO number (if configured) |
| `call_sign` | `TimestampedString` | Radio call sign (if configured) |
| `configuration_json` | `TimestampedString` | Full configuration JSON, published on startup and after `set_config` |

### CCRP — Consistent Common Reference Point (Navigation)

In the maritime domain, navigation systems have typically been calibrated to a single 2D reference point because true 3D sensors were uncommon. This is changing. Keelson defines the CCRP as a specific 3D point (x, y, z) in the platform's local coordinate system. The CCRP serves as the common anchor for all sensor and frame definitions, enabling consistent alignment across both 2D and 3D sources.

**The CCRP should be the common navigation point, it does not necessarily coincide with the local coordinate system's origin (0, 0, 0)**
