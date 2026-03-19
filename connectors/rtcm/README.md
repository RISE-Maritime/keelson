# RTCM

Bidirectional RTCM v3 connector for Keelson. Reads/writes RTCM correction data via stdin/stdout, using the Unix piping model for composability with external tools like `socat`.

**Tools:**

- [rtcm2keelson](#rtcm2keelson) (Ingest: stdin -> Keelson)
- [keelson2rtcm](#keelson2rtcm) (Distribute: Keelson -> stdout)
- [ntrip-cli](#ntrip-cli) (NTRIP v1 server: stdin -> NTRIP clients)

## Architecture

```
                            Zenoh bus
                               |
stdin -----> rtcm2keelson -----> keelson2rtcm -----> stdout
             (publisher)        (subscriber)           |
                                                       +---> socat (TCP)
                                                       +---> ntrip-cli (NTRIP v1)
```

Use `socat` or similar tools to bridge stdin/stdout to TCP, serial, or other transports.

## rtcm2keelson

Reads RTCM v3 correction frames from stdin, parses them using pyrtcm, and publishes each frame as a `TimestampedBytes` payload on the Keelson bus.

Stdin EOF triggers a clean shutdown. Malformed RTCM frames are logged and skipped.

### Usage

```
usage: rtcm2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                    [--connect CONNECT] [--listen LISTEN]
                    -r REALM -e ENTITY_ID -s SOURCE_ID
```

### Examples

```bash
# From a TCP base station (via socat)
socat TCP:rtcm-base.example.com:2101 STDOUT | \
  uv run python connectors/rtcm/bin/rtcm2keelson.py \
    --realm my-vessel --entity-id gnss --source-id base/0

# From a serial GNSS receiver
socat /dev/ttyUSB0,b115200 STDOUT | \
  uv run python connectors/rtcm/bin/rtcm2keelson.py \
    --realm my-vessel --entity-id gnss --source-id base/0

# From a file (for testing/replay)
cat rtcm_recording.bin | \
  uv run python connectors/rtcm/bin/rtcm2keelson.py \
    --realm my-vessel --entity-id gnss --source-id base/0
```

## keelson2rtcm

Subscribes to RTCM v3 data on the Keelson bus and writes raw RTCM bytes to stdout. Pipe the output to `socat` for TCP distribution or to `ntrip-cli` for NTRIP v1 serving.

### Usage

```
usage: keelson2rtcm [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                    [--connect CONNECT] [--listen LISTEN]
                    -r REALM -e ENTITY_ID [--source-id SOURCE_ID]
```

### Examples

```bash
# Distribute via bare TCP (using socat)
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel --entity-id gnss | \
  socat STDIN TCP-LISTEN:2102,reuseaddr

# Distribute via NTRIP (using ntrip-cli)
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel --entity-id gnss | \
  uv run python connectors/rtcm/bin/ntrip-cli.py \
    --port 2101 --mountpoint RTCM3

# Save to file
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel --entity-id gnss > rtcm_recording.bin
```

## ntrip-cli

Standalone NTRIP v1 server that reads raw RTCM bytes from stdin and distributes them to connected NTRIP clients. No Zenoh dependency — this is a pure networking tool.

### Usage

```
usage: ntrip-cli [-h] --port PORT [--host HOST]
                 [--mountpoint MOUNTPOINT] [--log-level LOG_LEVEL]
```

### Examples

```bash
# Serve RTCM from Keelson via NTRIP
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel --entity-id gnss | \
  uv run python connectors/rtcm/bin/ntrip-cli.py \
    --port 2101 --mountpoint RTCM3

# Serve RTCM from TCP base station via NTRIP (without Keelson)
socat TCP:rtcm-base.example.com:2101 STDOUT | \
  uv run python connectors/rtcm/bin/ntrip-cli.py \
    --port 2101 --mountpoint RTCM3
```

## Run in container

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "rtcm2keelson -h"
docker run --rm ghcr.io/rise-maritime/keelson "keelson2rtcm -h"
docker run --rm ghcr.io/rise-maritime/keelson "ntrip-cli -h"

# Ingest from a base station via socat
docker run --rm --network host ghcr.io/rise-maritime/keelson \
  "socat TCP:rtcm-base.example.com:2101 STDOUT | \
   rtcm2keelson --realm my-vessel --entity-id gnss --source-id base/0"

# Distribute via NTRIP
docker run --rm --network host ghcr.io/rise-maritime/keelson \
  "keelson2rtcm --realm my-vessel --entity-id gnss | \
   ntrip-cli --port 2101 --mountpoint RTCM3"
```

## Known Limitations

- **No NTRIP authentication.** The NTRIP v1 server does not support `Authorization` headers. Any client that can reach the server can connect and receive data. Use `--host 127.0.0.1` to restrict access to localhost, or handle access control at the network level (firewall, VPN).
- **No TLS.** RTCM data is transmitted unencrypted. Use a VPN or SSH tunnel if encryption is required.
- **NTRIP v1 only.** NTRIP v2 (HTTP/1.1 chunked transfer) is not supported.
