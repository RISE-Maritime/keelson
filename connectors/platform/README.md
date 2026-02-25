# platform

Contains helper binaries for providing (semi-)static data of a platform (such as a vessel) onto zenoh.

## `platform-geometry`

```
usage: platform-geomtry [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                        [--connect CONNECT] [--listen LISTEN] -r REALM -e
                        ENTITY_ID -s SOURCE_ID --config CONFIG
                        [--interval INTERVAL]

Command line utility tool for outputting geometrical information about a platform on a given interval

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --config CONFIG       A path to a JSON-encoded configuration file for this platform. (default: None)
  --interval INTERVAL   Interval (second) at which the information will be put to zenoh. (default: 10)
```

An example of the configuration file is provided in [`example-config.json`](./example-config.json).


## ISO 8855

Translation [x, y, z] meters

Rotation [roll, pitch, yaw] degrees

![alt text](image.png)
