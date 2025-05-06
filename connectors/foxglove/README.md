# foxglove

Contains binaries that interact with the foxglove SDK in different ways.

## `foxglove-liveview`

```
usage: foxglove-liveview [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT] -k KEY [--ws-host WS_HOST] [--ws-port WS_PORT]
                         [--extra-subjects-types EXTRA_SUBJECTS_TYPES]

A foxglove websocket server for keelson

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447 (default: None)
  -k KEY, --key KEY     Key expressions to subscribe to from the Zenoh session (default: None)
  --ws-host WS_HOST
  --ws-port WS_PORT
  --extra-subjects-types EXTRA_SUBJECTS_TYPES
                        Add additional well-known subjects and protobuf types as --extra-subjects-
                        types=path/to/subjects.yaml,path_to_protobuf_file_descriptor_set.bin (default: None)
```