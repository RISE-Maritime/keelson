# platform

Contains helper binaries for providing (semi-)static data of a platform (such as a vessel) onto zenoh.

## `platform-geometry`

```
usage: platform-geomtry [-h] [--log-level LOG_LEVEL] [--connect CONNECT] -r REALM -e ENTITY_ID -s SOURCE_ID --config CONFIG [--interval INTERVAL]

Command line utility tool for outputting geometrical information about a platform on a given interval

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --connect CONNECT     Endpoints to connect to. (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --config CONFIG       A path to a JSON-encoded configuration file for this platform. (default: None)
  --interval INTERVAL   Interval (second) at whic the information will be put to zenoh. (default: 10)
  ```

  An example of the configuration file is provided in [`example-config.json`](./example-config.json).

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

![alt text](./ShipLocalCoordinateSystem.png)

### Schema Structure

```json
{
  ...
  frame_transforms: array (required)
    └─ Array of coordinate frame transformations
       └─ [
            {
             ....         
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

### CCRP — Consistent Common Reference Point (Navigation)

In the maritime domain, navigation systems have typically been calibrated to a single 2D reference point because true 3D sensors were uncommon. This is changing. Keelson defines the CCRP as a specific 3D point (x, y, z) in the platform’s local coordinate system. The CCRP serves as the common anchor for all sensor and frame definitions, enabling consistent alignment across both 2D and 3D sources.

**The CCRP should be the common navigation point, it does not necessarily coincide with the local coordinate system’s origin (0, 0, 0)**