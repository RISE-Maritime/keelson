# foxglove

Contains binaries that interact with the foxglove SDK in different ways.

## `foxglove-liveview`

```
usage: foxglove-liveview [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                         [--connect CONNECT] [--listen LISTEN] -k KEY
                         [--ws-host WS_HOST] [--ws-port WS_PORT]
                         [--extra-subjects-types EXTRA_SUBJECTS_TYPES]
                         [--expose-rpc-services EXPOSE_RPC_SERVICES]
                         [--rpc-call-timeout RPC_CALL_TIMEOUT]

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
  --rpc-call-timeout RPC_CALL_TIMEOUT
                        Timeout (seconds) for RPC calls made on behalf of Foxglove clients. Each
                        call blocks one Foxglove handler thread for up to this long if the
                        responder is dead, so keep it as low as your slowest procedure allows.
                        (default: 10.0)
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
`{base_path}/{entity_id}/{interface}/{version}/{procedure}/{source_id}`,
matching the keelson RPC key's addressing components. The `base_path`
prefix keeps names unambiguous (and collision-free) when multiple
`--expose-rpc-services` base paths are monitored.

**Only well-known interfaces are advertised.** An `(interface, version)`
liveliness token is only turned into services if it's registered in this
SDK's bundled `interfaces.yaml`. A live interface that isn't well-known
(e.g. a newer interface served by a connector running a newer SDK release)
is logged as a warning and skipped — it does not stop the bridge from
advertising everything else.

**Calls are a raw byte passthrough.** The bridge forwards the Foxglove
request payload to the responder unmodified and returns the reply bytes
unmodified — no protobuf decode/re-encode in the middle. For protobuf
procedures this is equivalent to a typed call; for JSON-placeholder
procedures it's what makes them work at all (see below). A typed
`ErrorResponse` from the responder is surfaced to the Foxglove client as
an error string like `INVALID_STATE: no file loaded`; no reply within
`--rpc-call-timeout` surfaces as a timeout error naming the endpoint.

**JSON payloads (`configurable/v1`).** `configurable/v1`'s
`get_config`/`set_config` procedures use a `JSON{}` protobuf message as a
placeholder — the actual request/response bytes are a raw JSON string,
not a protobuf payload. The bridge detects placeholder sides (message
full name ending in `.JSON`) and advertises them with `json` encoding and
a permissive JSON schema, so Foxglove presents a JSON input and displays
a JSON response. Combined with the raw passthrough, `configurable/v1`
services are fully callable from Foxglove: type JSON in, get JSON back.

**Threading note.** Each service call runs the zenoh query synchronously
(blocking up to `--rpc-call-timeout`, default 10 s) on the Foxglove
server's own handler thread. This is fine for v1 given keelson's
single-reply RPC semantics, but a dead responder pins that one handler
thread for the full timeout — keep the timeout as low as your slowest
procedure allows.
