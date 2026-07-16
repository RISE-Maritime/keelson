# foxglove

Contains binaries that interact with the foxglove SDK in different ways.

## `foxglove-liveview`

```
usage: foxglove-liveview [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                         [--connect CONNECT] [--listen LISTEN] -k KEY
                         [--ws-host WS_HOST] [--ws-port WS_PORT]
                         [--extra-subjects-types EXTRA_SUBJECTS_TYPES]
                         [--expose-rpc-services EXPOSE_RPC_SERVICES]

A foxglove websocket server for keelson

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -k KEY, --key KEY     Key expressions to subscribe to from the Zenoh session (default: None)
  --ws-host WS_HOST     (default: 127.0.0.1)
  --ws-port WS_PORT     (default: 8765)
  --extra-subjects-types EXTRA_SUBJECTS_TYPES
                        Add additional well-known subjects and protobuf types as --extra-subjects-
                        types=path/to/subjects.yaml,path_to_protobuf_file_descriptor_set.bin (default: None)
  --expose-rpc-services EXPOSE_RPC_SERVICES
                        Advertise all live keelson RPC endpoints under this base path as Foxglove
                        services (default: None)
```

### RPC services

Passing `--expose-rpc-services BASE_PATH` (repeatable, one per base path/realm)
turns on a bridge that advertises every live keelson RPC endpoint under that
base path as a callable Foxglove service. Discovery is driven entirely by
interface-level liveliness — connectors don't need any foxglove-specific
code to show up:

- When an `(interface, version)` liveliness token joins the bus (e.g. a
  connector starts serving `replay_control/v1` via
  `keelson.scaffolding.serve_rpc`), one Foxglove service is advertised per
  procedure defined by that interface version.
- When the token leaves (the connector stops or disconnects), its services
  are removed from the Foxglove server.
- Foxglove Studio / app users connected to the websocket see services
  appear and disappear live as connectors come and go — no restart of
  `foxglove-liveview` required.

**Service naming.** Each service is named
`{entity_id}/{interface}/{version}/{procedure}/{source_id}`, matching the
keelson RPC key's addressing components.

**Only well-known interfaces are advertised.** An `(interface, version)`
liveliness token is only turned into services if it's registered in this
SDK's bundled `interfaces.yaml`. A live interface that isn't well-known
(e.g. a newer interface served by a connector running a newer SDK release)
is logged as a warning and skipped — it does not stop the bridge from
advertising everything else.

**JSON payload caveat for `configurable/v1`.** `configurable/v1`'s
`get_config`/`set_config` procedures use a `JSON{}` message as a
placeholder — it isn't a real protobuf-typed payload, it's a raw JSON
string carried as the request/response bytes. Callers invoking these two
services through Foxglove should send/expect a raw JSON string, not a
`JSON` protobuf message.

**Threading note.** Each service call runs `invoke_procedure` synchronously
(blocking up to a 10s timeout) on the Foxglove server's own handler thread.
This is fine for v1 given keelson's single-reply RPC semantics: a slow or
unresponsive responder only holds up that one handler thread.
