# keelson-connector-tak

Bi-directional bridge between a TAK / CoT server (e.g. [taky](https://github.com/tkuester/taky), FreeTAKServer, TAK Server) and the Keelson bus. Part of the [keelson monorepo](https://github.com/RISE-Maritime/keelson).

* `keelson2tak` — reads keelson subjects describing the local entity (position, course, speed, name) and emits CoT XML events to a TAK server over TCP or TLS.
* `tak2keelson` — subscribes to a TAK server, parses inbound CoT events, and republishes them as keelson `@target/cot_{uid}` subjects (same pattern as `ais2keelson`'s `@target/mmsi_{mmsi}`).

Only the XML CoT wire format is supported. The protobuf "TAK Protocol v1" (mesh/stream), UDP multicast mesh mode, GeoChat, and CoT data-package *attachments* (mission packages sent over the wire) are out of scope.

> **Connection config from a TAK data/pref package.** A *pref package* — the `atak.zip` a TAK admin hands out for client enrollment — bundles the server `connectString` plus the client/CA certificates. Both binaries accept it via `--tak-data-package` as an alternative to the explicit `--tak-url` + `--tak-client-cert/--tak-client-key/--tak-ca` flags; pytak unzips it, reads the `.pref`, and converts the PKCS#12 keystore to PEM. The package contains a private key and keystore passwords — keep it out of version control (`atak.zip` is gitignored).

## Subject mapping

Only fields that map cleanly onto existing keelson subjects are handled. Everything else is dropped (inbound) or omitted (outbound).

### Inbound (`tak2keelson`)

Per-target under `@target/cot_{sanitized_uid}`. Non-alphanumeric UID characters (outside `[a-zA-Z0-9_-]`) are replaced with `_`.

| CoT field | Keelson subject | Notes |
|---|---|---|
| `point/@lat`, `@lon`, `@hae` | `location_fix` | altitude carried when `hae` is present |
| `point/@ce` | `location_fix_accuracy_horizontal_m` | skipped when `9999999.0` |
| `point/@le` | `location_fix_accuracy_vertical_m` | skipped when `9999999.0` |
| `detail/track/@course` | `course_over_ground_deg` | degrees true |
| `detail/track/@speed` | `speed_over_ground_knots` | CoT is m/s; `knots = mps * 1.94384` |
| `detail/contact/@callsign` | `name` | |
| `detail/status/@battery` | `battery_state_of_charge_pct` | |

Events whose `stale` deadline is already in the past are dropped without any publish.

### Outbound (`keelson2tak`)

Triggered by `location_fix` updates (throttled via `--emit-at-most-every`) and by `--emit-period` ticks. The other subjects are read from the skarv cache.

| Keelson subject | CoT field | Notes |
|---|---|---|
| `location_fix` (lat/lon/altitude) | `point/@lat`, `@lon`, `@hae` | `hae` defaults to `0.0` |
| `location_fix_accuracy_horizontal_m` | `point/@ce` | `9999999.0` when absent |
| `location_fix_accuracy_vertical_m` | `point/@le` | `9999999.0` when absent |
| `course_over_ground_deg` | `detail/track/@course` | |
| `speed_over_ground_knots` | `detail/track/@speed` | emitted in m/s |
| `name` | `detail/contact/@callsign` | falls back to `--cot-callsign` |

Every event also carries `event/@uid`, `@type`, `@how`, `@time`, `@start`, `@stale` from CLI flags plus the emission time.

## Usage

### `tak2keelson`

```
usage: tak2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                   -s SOURCE_ID (--tak-url TAK_URL | --tak-data-package TAK_DATA_PACKAGE)
                   [--tak-client-cert TAK_CLIENT_CERT] [--tak-client-key TAK_CLIENT_KEY]
                   [--tak-ca TAK_CA] [--tak-insecure] [--reconnect-delay RECONNECT_DELAY]
                   [--publish-raw] [--target-timeout-s TARGET_TIMEOUT_S]

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
  --tak-url TAK_URL     TAK server URL, e.g. tls://host:8089 (default: None)
  --tak-data-package TAK_DATA_PACKAGE
                        Path to a TAK data/pref package (.zip); provides COT_URL + TLS certs
                        (default: None)
  --tak-client-cert TAK_CLIENT_CERT
  --tak-client-key TAK_CLIENT_KEY
  --tak-ca TAK_CA
  --tak-insecure
  --reconnect-delay RECONNECT_DELAY
  --publish-raw
  --target-timeout-s TARGET_TIMEOUT_S
```

### `keelson2tak`

```
usage: keelson2tak [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                   (--tak-url TAK_URL | --tak-data-package TAK_DATA_PACKAGE)
                   [--tak-client-cert TAK_CLIENT_CERT] [--tak-client-key TAK_CLIENT_KEY]
                   [--tak-ca TAK_CA] [--tak-insecure] [--reconnect-delay RECONNECT_DELAY]
                   --cot-uid COT_UID [--cot-type COT_TYPE] [--cot-callsign COT_CALLSIGN]
                   [--cot-how COT_HOW] [--cot-stale-seconds COT_STALE_SECONDS]
                   [--emit-at-most-every EMIT_AT_MOST_EVERY] [--emit-period EMIT_PERIOD]
                   [--source_id_location_fix SOURCE_ID_LOCATION_FIX]
                   [--source_id_location_fix_accuracy_horizontal_m SOURCE_ID_LOCATION_FIX_ACCURACY_HORIZONTAL_M]
                   [--source_id_location_fix_accuracy_vertical_m SOURCE_ID_LOCATION_FIX_ACCURACY_VERTICAL_M]
                   [--source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG]
                   [--source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS]
                   [--source_id_name SOURCE_ID_NAME]

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
  --tak-url TAK_URL     TAK server URL, e.g. tls://host:8089 (default: None)
  --tak-data-package TAK_DATA_PACKAGE
                        Path to a TAK data/pref package (.zip); provides COT_URL + TLS certs
                        (default: None)
  --tak-client-cert TAK_CLIENT_CERT
  --tak-client-key TAK_CLIENT_KEY
  --tak-ca TAK_CA
  --tak-insecure
  --reconnect-delay RECONNECT_DELAY
  --cot-uid COT_UID
  --cot-type COT_TYPE
  --cot-callsign COT_CALLSIGN
  --cot-how COT_HOW
  --cot-stale-seconds COT_STALE_SECONDS
  --emit-at-most-every EMIT_AT_MOST_EVERY
  --emit-period EMIT_PERIOD
  --source_id_location_fix SOURCE_ID_LOCATION_FIX
  --source_id_location_fix_accuracy_horizontal_m SOURCE_ID_LOCATION_FIX_ACCURACY_HORIZONTAL_M
  --source_id_location_fix_accuracy_vertical_m SOURCE_ID_LOCATION_FIX_ACCURACY_VERTICAL_M
  --source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG
  --source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS
  --source_id_name SOURCE_ID_NAME
```

## Examples

Plain TCP to taky:

```bash
keelson2tak \
  --realm rise --entity-id landkrabban \
  --tak-url tcp://taky.local:8087 \
  --cot-uid rise-landkrabban-self \
  --cot-type a-f-S-X \
  --cot-callsign LANDKRABBAN
```

Mutual TLS:

```bash
keelson2tak \
  --realm rise --entity-id landkrabban \
  --tak-url tls://taky.example.com:8089 \
  --tak-client-cert /etc/keelson/tak-client.pem \
  --tak-client-key /etc/keelson/tak-client.key \
  --tak-ca /etc/keelson/tak-ca.pem \
  --cot-uid rise-landkrabban-self
```

From a TAK data package (server URL + certs come from the package):

```bash
keelson2tak \
  --realm rise --entity-id landkrabban \
  --tak-data-package /etc/keelson/atak.zip \
  --cot-uid rise-landkrabban-self
```

TAK -> keelson:

```bash
tak2keelson \
  --realm rise --entity-id landkrabban \
  --source-id tak/0 \
  --tak-url tcp://taky.local:8087 \
  --publish-raw
```

### docker-compose

```yaml
services:

  sink-keelson2tak:
    image: ghcr.io/rise-maritime/keelson
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "keelson2tak -r <realm> -e <entity> --tak-url tcp://taky.local:8087 --cot-uid <uid> --cot-callsign <callsign>"
      ]

  source-tak2keelson:
    image: ghcr.io/rise-maritime/keelson
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "tak2keelson -r <realm> -e <entity> -s tak/0 --tak-url tcp://taky.local:8087 --publish-raw"
      ]
```
