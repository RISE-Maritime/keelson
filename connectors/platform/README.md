# platform

Contains helper binaries for providing (semi-)static data of a platform (such as a vessel) for keelson services.

## `platform-geometry2keelson`

```
usage: platform-geometry2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                                 [--connect CONNECT] [--listen LISTEN]
                                 [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID -s SOURCE_ID
                                 --config CONFIG [--interval INTERVAL]

Command line utility tool for outputting geometrical information about a platform on a given
interval

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode, -m {peer,client}
                        The Zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  --zenoh-config ZENOH_CONFIG
                        Path to a Zenoh configuration file (JSON5). Everything the flags above
                        cannot express — access control, QoS defaults, transport tuning — lives
                        here. --mode/--connect/--listen still win where they overlap. Falls back
                        to the ZENOH_CONFIG environment variable. (default: None)
  -r, --realm REALM
  -e, --entity-id ENTITY_ID
  -s, --source-id SOURCE_ID
  --config CONFIG       A path to a JSON-encoded configuration file for this platform.
  --interval INTERVAL   Interval (second) at which the information will be put to zenoh. (default:
                        10)
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

### Georeferencing a fixed installation

`frame_transforms` describe a platform's internal geometry, but they are purely relative: on their own a transform tree is a local construction with no tie to the world. A `sensor_station` — a mast, tower or quayside rig surveyed against a site — closes that gap with `position`:

```json
"position": {
  "frame_id": "storakrabban/base_link",
  "latitude_deg": 58.264407,
  "longitude_deg": 12.248259,
  "accuracy_horizontal_m": 1.3
}
```

`frame_id` names the frame whose **origin** sits at that latitude/longitude — this is `foxglove.LocationFix.frame_id` semantics, and it is the mechanism keelson uses to georeference a transform tree. Point it at the root of your `frame_transforms` and everything else hangs off it. Left out, it defaults to that root when the tree has exactly one, and to the entity id otherwise. See `example-config-sensor-station.json`.

`frame_id` is the part that is easiest to get wrong and hardest to notice. It is the only link from the position to the platform's node in the transform tree, so it is what tells a consumer which way the installation faces. Name a frame that is not in the tree and the sensors still render, just unrotated, as ordinary-looking point clouds pointing the wrong way. The connector therefore logs a warning, at start-up and after every `set_config`, when `frame_id` names no frame in a non-empty `frame_transforms`, and when a `vessel` is given a `position`.

`accuracy_horizontal_m` is **drms** (`sqrt(sigma_E^2 + sigma_N^2)`); it is published on its own subject and also fills `position_covariance` with a per-axis variance of `drms^2 / 2` and `position_covariance_type = APPROXIMATED`. An axis you do not state gets a deliberately wide variance rather than zero, because zero would claim perfect knowledge of the thing that was not surveyed.

Omit `position` for a `vessel`: a moving platform's position belongs on the same subject from its GNSS receiver, and a static value here would silently contradict it.

There is no latched delivery in keelson — the position is republished every `--interval` like everything else here, so a late subscriber waits at most one interval.

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
  platform_type: string (optional)    - One of: "vessel", "sensor_station", "operator_station"
  description: string (optional)      - Human-readable description of the platform
  name: string (optional)
  length_over_all_m: number (optional)
  breadth_over_all_m: number (optional)
  mmsi_number: integer (optional)     - MMSI for AIS identification
  imo_number: integer (optional)      - IMO number (0 if not assigned)
  call_sign: string (optional)        - Radio call sign

  operational_limits: object (optional)
    └─ Capability envelope of this platform: what it was validated to do,
       context-free. A route's limits are passage restrictions and never
       widen it (protocol specification §6.8 item 8, #226)
       ├─ max_speed_knots: number
       ├─ max_wind_speed_mps: number
       ├─ max_wave_height_m: number
       ├─ max_range_m: number
       └─ endurance_hours: number

  calibration: object (optional)
    └─ How the numbers were measured, and how well (see "Calibration provenance")
       ├─ frame: string
       ├─ zero: {latitude, longitude, accuracy_m, heading_deg, capture, samples, ...}
       ├─ sensors: [{child_frame_id, capture, accuracy_m, rotation_accuracy_deg, ...}]
       └─ updated_at_ms: integer

  ccrp_m: object (optional)
    └─ Consistent Common Reference Point in the platform local coordinate system
       ├─ x: number (forward, meters)
       ├─ y: number (starboard, meters)
       └─ z: number (down, meters)

  position: object (optional)
    └─ Surveyed WGS84 position of a fixed installation (see "Georeferencing a fixed installation")
       ├─ frame_id: string (optional; default: the frame_transforms root, else the entity id)
       ├─ latitude_deg: number (required)
       ├─ longitude_deg: number (required)
       ├─ altitude_m: number (optional)
       ├─ accuracy_horizontal_m: number (optional, drms)
       └─ accuracy_vertical_m: number (optional, one sigma)

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

  stream_mounts: object (optional)
    └─ Which mount each position stream is measured at
       ├─ v: 1
       └─ streams: object
          └─ "<concrete pubsub key>": "<child_frame_id>"
}
```

### Stream mounts

A platform often publishes several `location_fix` streams, each from its own
antenna. `frame_transforms` says where the mounts are; `stream_mounts` says
which stream is measured at which of them, so a consumer can move a fix from
the antenna to the CCRP:

```json
"stream_mounts": {
  "v": 1,
  "streams": {
    "rise/@v0/ssrs18/pubsub/location_fix/rutx": "ssrs18-demo-frame-gnss-rutx"
  }
}
```

A publisher that can be told its mount says so itself, in
`LocationFix.frame_id` (e.g. `mavlink2keelson --gps-raw-frame-id`,
`nmea01832keelson --frame-id`); `stream_mounts` is for the ones that cannot.
Where both exist the declared link wins. The full consumer rule is in the
protocol specification, §2.2.1 "Where a fix is measured".

Keys are concrete — one stream each, no wildcards. The connector logs a
warning, at start-up and after `set_config`, for a key that is not a concrete
pubsub key and for a mount that names no `child_frame_id` in
`frame_transforms`; it does not refuse the configuration.

### Propulsion

`propulsion` lists the devices that move and steer the platform — propellers,
rudders, thrusters, azimuths and waterjets — each with an `id`, a `kind` and
its position relative to CCRP. The `id` is the name the device goes by on the
bus: a `vehicle_control/v1` mapping addresses it as `<axis>/<id>` (for example
`thruster/bow-1`), and its reports are published with the `id` as the last
`source_id` chunk. A single-screw, single-rudder platform may leave it out;
bare `steering` and `throttle` still mean her one device.

## Finding platforms

There is no platform registry on the bus, and none is needed: every platform
connector serves its configuration on `configurable/v1`, and that configuration
always carries `platform_type` (`vessel` when the file leaves it out), so a
client asks every configurable source at once and keeps the platform
documents. In Python:

```python
from keelson.scaffolding import list_platforms

for p in list_platforms(session, "rise"):
    print(p.entity_id, p.source_id, p.config["platform_type"], p.config.get("name"))
```

What a platform publishes comes from liveliness, not from a procedure
(`list_publications`, protocol specification §5.5, keelson #191).

## Calibration provenance

The optional `calibration` block says how each number was measured and how
well: the zero point as surveyed (position, its accuracy and spread, the
heading and what it was measured along) and, per `frame_transforms` row, how
its translation and rotation were captured and their accuracy. An **absent
accuracy means unknown**, never zero: zero would claim a perfect measurement.
`capture` and `heading_source` are open vocabularies; the values in use are
listed in the schema.

It is the block the Logline calibration app writes (its `docs/calibration.md`),
so a calibration made there validates here unchanged instead of losing its
uncertainty on export. A `calibration.sensors[]` row naming a frame that is not
in `frame_transforms` is warned about at start-up and after `set_config`, not
refused.

## Queryable Configuration Interface

The connector exposes a live configuration interface via Zenoh RPC, following the standard Keelson `Configurable` interface:

| Procedure | Key pattern | Description |
|---|---|---|
| `get_config` | `{realm}/@v0/{entity_id}/@rpc/configurable/v1/get_config/{source_id}` | Returns the full current configuration as JSON |
| `set_config` | `{realm}/@v0/{entity_id}/@rpc/configurable/v1/set_config/{source_id}` | Replaces the running configuration (validated against the schema) |

Configuration changes applied via `set_config` take effect on the next publish interval. The updated config is also published to the `configuration_json` subject.

### Published Subjects

| Subject | Type | Description |
|---|---|---|
| `length_over_all_m` | `TimestampedFloat` | Length over all in meters |
| `breadth_over_all_m` | `TimestampedFloat` | Breadth over all in meters |
| `draught_max_m` | `TimestampedFloat` | Maximum draught in meters (if configured) |
| `frame_transform` | `foxglove.FrameTransform` | One message per configured frame transform |
| `mmsi_number` | `TimestampedInt` | MMSI number (if configured) |
| `imo_number` | `TimestampedInt` | IMO number (if configured) |
| `call_sign` | `TimestampedString` | Radio call sign (if configured) |
| `location_fix` | `foxglove.LocationFix` | Surveyed position of a fixed installation (if `position` is configured) |
| `location_fix_accuracy_horizontal_m` | `TimestampedFloat` | Horizontal accuracy as drms (if configured) |
| `location_fix_accuracy_vertical_m` | `TimestampedFloat` | Vertical one-sigma accuracy (if configured) |
| `configuration_json` | `TimestampedString` | Full configuration JSON, published on startup and after `set_config` |

### Draught and under-keel clearance

Two of the vessel's own values that under-keel monitoring and chart safety start from:

- `draught_max_m`, top level: her deepest point below the waterline in her deepest loaded condition.
  It is published on the `draught_max_m` subject, like length and breadth. It is not
  `Route.Leg.draught_max_m`, a waterway's limit (a lock, a channel), and not
  `RouteInfo.vessel_draft_m`, which is what one route was planned for.
- `operational_limits.min_ukc_static_m`: the static under-keel clearance she must keep, the
  company's UKC policy for this vessel. A route leg's `ukc_static_m` is the value planned for that
  leg.

Chart safety settings (safety contour, safety depth, shallow and deep contours) are not here. They
are an ECDIS's, derived from these two values.

### CCRP — Consistent Common Reference Point (Navigation)

In the maritime domain, navigation systems have typically been calibrated to a single 2D reference point because true 3D sensors were uncommon. This is changing. Keelson defines the CCRP as a specific 3D point (x, y, z) in the platform's local coordinate system. The CCRP serves as the common anchor for all sensor and frame definitions, enabling consistent alignment across both 2D and 3D sources.

**The CCRP should be the common navigation point, it does not necessarily coincide with the local coordinate system's origin (0, 0, 0)**
