# RTCM

Bidirectional RTCM v3 connector for Keelson. Bridges RTCM correction data between TCP base stations and the Keelson bus, and distributes it to rovers via bare TCP or NTRIP v1.

**Tools:**

- [rtcm2keelson](#rtcm2keelson) (Ingest: TCP base station -> Keelson)
- [keelson2rtcm](#keelson2rtcm) (Distribute: Keelson -> TCP/NTRIP clients)

## Architecture

```
                          Zenoh bus
                             |
TCP Base Station ----> rtcm2keelson ----> keelson2rtcm ----> TCP clients
  (RTCM stream)        (publisher)        (subscriber)       (rovers)
                                              |
                                              +----> NTRIP v1 clients
                                                      (rovers)
```

## rtcm2keelson

Connects to a TCP base station streaming RTCM v3 corrections, parses frames using pyrtcm, and publishes each frame as a `TimestampedBytes` payload on the Keelson bus.

Automatically reconnects with exponential backoff (1s to 60s) on connection loss. Malformed RTCM frames are logged and skipped without dropping the connection.

### Usage

```
usage: rtcm2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                    [--connect CONNECT] [--listen LISTEN]
                    -r REALM -e ENTITY_ID -s SOURCE_ID
                    --host HOST --port PORT
```

### Example

```bash
uv run python connectors/rtcm/bin/rtcm2keelson.py \
  --realm my-vessel \
  --entity-id gnss \
  --source-id base/0 \
  --host rtcm-base.example.com \
  --port 2101
```

## keelson2rtcm

Subscribes to RTCM v3 data on the Keelson bus and distributes it to connected clients via bare TCP and/or NTRIP v1 server. Both servers can run simultaneously on separate ports.

### Usage

```
usage: keelson2rtcm [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                    [--connect CONNECT] [--listen LISTEN]
                    -r REALM -e ENTITY_ID [--source-id SOURCE_ID]
                    [--server-host SERVER_HOST]
                    [--tcp-port TCP_PORT] [--ntrip-port NTRIP_PORT]
                    [--mountpoint MOUNTPOINT]
```

At least one of `--tcp-port` or `--ntrip-port` is required.

### Examples

```bash
# TCP only
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss \
  --tcp-port 2102

# NTRIP only
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss \
  --ntrip-port 2101 \
  --mountpoint RTCM3

# Both TCP and NTRIP simultaneously
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss \
  --tcp-port 2102 \
  --ntrip-port 2101 \
  --mountpoint RTCM3
```

### Run in container

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "rtcm2keelson -h"
docker run --rm ghcr.io/rise-maritime/keelson "keelson2rtcm -h"

# Ingest from a base station
docker run --rm --network host ghcr.io/rise-maritime/keelson \
  "rtcm2keelson --realm my-vessel --entity-id gnss --source-id base/0 \
   --host rtcm-base.example.com --port 2101"

# Distribute via NTRIP
docker run --rm --network host ghcr.io/rise-maritime/keelson \
  "keelson2rtcm --realm my-vessel --entity-id gnss \
   --ntrip-port 2101 --mountpoint RTCM3"
```

## Known Limitations

- **No NTRIP authentication.** The NTRIP v1 server does not support `Authorization` headers. Any client that can reach the server can connect and receive data. Use `--server-host 127.0.0.1` (instead of the default `0.0.0.0`) to restrict access to localhost, or handle access control at the network level (firewall, VPN).
- **No TLS.** RTCM data is transmitted unencrypted. Use a VPN or SSH tunnel if encryption is required.
- **NTRIP v1 only.** NTRIP v2 (HTTP/1.1 chunked transfer) is not supported.
