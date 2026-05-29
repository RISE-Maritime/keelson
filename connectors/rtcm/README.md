# RTCM connector for Keelson

Bidirectional RTCM v3 connector tools for Keelson.

These tools read, publish, subscribe, and distribute RTCM correction data using Keelson/Zenoh and the Unix piping model. They are intended to be composable with external tools such as `socat`, serial devices, TCP streams, NTRIP casters, and GNSS receivers.

## Tools

* [`rtcm2keelson`](#rtcm2keelson): ingest RTCM from `stdin` and publish it to Keelson.
* [`keelson2rtcm`](#keelson2rtcm): subscribe to RTCM from Keelson and write raw RTCM to `stdout`.
* [`ntrip2keelson`](#ntrip2keelson): connect to an authenticated NTRIP caster, send rover position as GGA, receive RTCM corrections, and publish them to Keelson.
* [`ntrip-cli`](#ntrip-cli): local NTRIP v1 server that reads RTCM from `stdin` and serves it to NTRIP clients.

## Architecture

```text
                                Keelson / Zenoh bus
                                      |
                                      |
stdin -----> rtcm2keelson ------------+-------------> keelson2rtcm -----> stdout
             (publisher)              |             (subscriber)
                                      |  
                                      |                 +---> socat -> serial GNSS receiver
                                      |                 +---> socat -> TCP
                                      |                 +---> ntrip-cli
                                      |
NTRIP caster -----> ntrip2keelson ----+
                  (authenticated       
                   NTRIP client)       
                                       
```

Use `socat` or similar tools to bridge `stdin`/`stdout` to TCP, serial ports, files, or other transports.

---

## rtcm2keelson

Reads RTCM v3 correction frames from `stdin`, parses them using `pyrtcm`, and publishes each frame as a `TimestampedBytes` payload on the Keelson bus.

`stdin` EOF triggers a clean shutdown. Malformed RTCM frames are logged and skipped.

### Usage

```text
usage: rtcm2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                    [--connect CONNECT] [--listen LISTEN]
                    -r REALM -e ENTITY_ID -s SOURCE_ID
```

### Examples

```bash
# From a TCP base station via socat
socat TCP:rtcm-base.example.com:2101 STDOUT | \
  uv run python connectors/rtcm/bin/rtcm2keelson.py \
    --realm my-vessel \
    --entity-id gnss \
    --source-id base/0

# From a serial GNSS receiver
socat /dev/serial/by-id/usb-example-gnss,b115200,raw,echo=0 STDOUT | \
  uv run python connectors/rtcm/bin/rtcm2keelson.py \
    --realm my-vessel \
    --entity-id gnss \
    --source-id base/0

# From a file, for testing/replay
cat rtcm_recording.bin | \
  uv run python connectors/rtcm/bin/rtcm2keelson.py \
    --realm my-vessel \
    --entity-id gnss \
    --source-id base/0
```

---

## keelson2rtcm

Subscribes to RTCM v3 data on the Keelson bus and writes raw RTCM bytes to `stdout`.

Pipe the output to `socat` for TCP distribution, to a serial GNSS receiver, or to `ntrip-cli` for local NTRIP serving.

### Usage

```text
usage: keelson2rtcm [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                    [--connect CONNECT] [--listen LISTEN]
                    -r REALM -e ENTITY_ID [--source-id SOURCE_ID]
```

### Examples

```bash
# Distribute via bare TCP using socat
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss | \
  socat STDIN TCP-LISTEN:2102,reuseaddr

# Write RTCM corrections to a GNSS receiver over serial
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss | \
  socat -u STDIN /dev/serial/by-id/usb-example-gnss,b115200,raw,echo=0

# Distribute via local NTRIP server
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss | \
  uv run python connectors/rtcm/bin/ntrip-cli.py \
    --port 2101 \
    --mountpoint RTCM3

# Save RTCM stream to file
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss > rtcm_recording.bin
```

---

## ntrip2keelson

Connects to an authenticated NTRIP caster, sends the rover position as periodic NMEA GGA messages, receives RTCM correction frames from the caster, and publishes them to Keelson as `raw_rtcm_v3`.

This is intended for network RTK services where the caster requires:

1. Server authentication.
2. A mountpoint.
3. Periodic rover position feedback, usually as GGA.
4. A bidirectional TCP connection where GGA is sent upstream and RTCM corrections are received downstream.

The rover position can be provided by subscribing to a Keelson `location_fix` subject. An initial latitude/longitude/altitude can also be provided, which is useful when the NTRIP caster requires a GGA position before it starts streaming corrections.

### Usage

```text
usage: ntrip2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                     [--connect CONNECT] [--listen LISTEN]
                     -r REALM -e ENTITY_ID -s SOURCE_ID
                     --caster-host CASTER_HOST
                     [--caster-port CASTER_PORT]
                     --mountpoint MOUNTPOINT
                     --username USERNAME
                     [--password PASSWORD]
                     [--password-env PASSWORD_ENV]
                     [--position-source-id POSITION_SOURCE_ID]
                     [--initial-latitude INITIAL_LATITUDE]
                     [--initial-longitude INITIAL_LONGITUDE]
                     [--initial-altitude INITIAL_ALTITUDE]
                     [--gga-period GGA_PERIOD]
                     [--ntrip-version {1,2}]
```

### Example: local development

```bash
export NTRIP_USERNAME="<ntrip-username>"
export NTRIP_PASSWORD="<ntrip-password>"

uv run python connectors/rtcm/bin/ntrip2keelson.py \
  --realm rise \
  --entity-id case \
  --source-id ntrip/primary \
  --caster-host nrtk-swepos.lm.se \
  --caster-port 8500 \
  --mountpoint MSM_GNSS \
  --username "$NTRIP_USERNAME" \
  --password-env NTRIP_PASSWORD \
  --position-source-id 'ardusimple/**' \
  --initial-latitude 57.68900200 \
  --initial-longitude 11.97563790 \
  --initial-altitude 62.24 \
  --gga-period 5 \
  --ntrip-version 2 \
  --log-level 20
```

### Example: Docker Compose

This example passes the NTRIP username and password as environment variables. The password is read by `ntrip2keelson` using `--password-env`, which avoids putting the password directly into the command line.

```yaml
services:
  ntrip2keelson:
    image: ghcr.io/rise-maritime/keelson:0.5.1-pre.2
    network_mode: host
    environment:
      NTRIP_USERNAME: "<ntrip-username>"
      NTRIP_PASSWORD: "<ntrip-password>"
    command:
      - >-
        ntrip2keelson
        --realm rise
        --entity-id case
        --source-id ntrip/primary
        --caster-host nrtk-swepos.lm.se
        --caster-port 8500
        --mountpoint MSM_GNSS
        --username "$${NTRIP_USERNAME}"
        --password-env NTRIP_PASSWORD
        --position-source-id 'ardusimple/**'
        --initial-latitude 57.68900200
        --initial-longitude 11.97563790
        --initial-altitude 62.24
        --gga-period 5
        --ntrip-version 2
        --log-level 20
    restart: unless-stopped
```

Run:

```bash
docker compose up -d ntrip2keelson
docker compose logs -f ntrip2keelson
```

### Forwarding the RTCM stream to a rover over serial

`ntrip2keelson` publishes RTCM onto Keelson. To send those corrections to a GNSS receiver, run `keelson2rtcm` and pipe the output to the receiver serial port.

Use a stable serial path such as `/dev/serial/by-id/...` instead of `/dev/ttyUSB0`, because `/dev/ttyUSB0` can change after reboot or when USB devices are reconnected.

```yaml
services:
  keelson2rtcm_rover:
    image: ghcr.io/rise-maritime/keelson:0.5.1-pre.2
    network_mode: host
    devices:
      - /dev/serial/by-id/usb-example-gnss:/dev/serial/by-id/usb-example-gnss
    command:
      - >-
        keelson2rtcm
        --realm rise
        --entity-id case
        --source-id ntrip/primary
        --log-level 20
        |
        socat -u STDIN /dev/serial/by-id/usb-example-gnss,b115200,raw,echo=0
    restart: unless-stopped
```

To list stable serial paths:

```bash
ls -l /dev/serial/by-id/
```

---

## Safe credential practices

NTRIP credentials should be treated as operational secrets.

Recommended practices:

* Do not commit real usernames, passwords, mountpoint credentials, or deployment-specific account names to Git.
* Use placeholders in documentation, for example `<ntrip-username>` and `<ntrip-password>`.
* Prefer `--password-env NTRIP_PASSWORD` over passing the password directly as `--password ...`.
* Avoid putting passwords directly in shell commands, because they may appear in shell history and process listings.
* Avoid putting real credentials directly in `docker-compose.yml` for shared repositories.
* Inject credentials through the deployment environment, a secret manager, CI/CD variables, Docker secrets, Kubernetes secrets, systemd credentials, Vault, or another controlled runtime mechanism.
* Restrict access to hosts that run NTRIP-connected services.
* Rotate credentials if they have been committed, shared in logs, pasted into tickets, or exposed in screenshots.
* Review logs before sharing them externally. Some tools may echo environment, command-line arguments, URLs, or authentication-related errors.

Example using runtime-provided environment variables:

```bash
export NTRIP_USERNAME="<ntrip-username>"
export NTRIP_PASSWORD="<ntrip-password>"

docker compose up -d ntrip2keelson
```

In this pattern, the compose file can reference the environment variables without storing the real credential values in the repository.

---

## ntrip-cli

Standalone NTRIP v1 server that reads raw RTCM bytes from `stdin` and distributes them to connected NTRIP clients.

This tool has no Zenoh dependency. It is a pure networking tool.

### Usage

```text
usage: ntrip-cli [-h] --port PORT [--host HOST]
                 [--mountpoint MOUNTPOINT] [--log-level LOG_LEVEL]
```

### Examples

```bash
# Serve RTCM from Keelson via NTRIP
uv run python connectors/rtcm/bin/keelson2rtcm.py \
  --realm my-vessel \
  --entity-id gnss | \
  uv run python connectors/rtcm/bin/ntrip-cli.py \
    --port 2101 \
    --mountpoint RTCM3

# Serve RTCM from TCP base station via NTRIP without Keelson
socat TCP:rtcm-base.example.com:2101 STDOUT | \
  uv run python connectors/rtcm/bin/ntrip-cli.py \
    --port 2101 \
    --mountpoint RTCM3
```

---

## Run in container

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson:0.5.1-pre.2 "rtcm2keelson -h"
docker run --rm ghcr.io/rise-maritime/keelson:0.5.1-pre.2 "keelson2rtcm -h"
docker run --rm ghcr.io/rise-maritime/keelson:0.5.1-pre.2 "ntrip2keelson -h"
docker run --rm ghcr.io/rise-maritime/keelson:0.5.1-pre.2 "ntrip-cli -h"

# Ingest from a base station via socat
docker run --rm --network host ghcr.io/rise-maritime/keelson:0.5.1-pre.2 \
  "socat TCP:rtcm-base.example.com:2101 STDOUT | \
   rtcm2keelson --realm my-vessel --entity-id gnss --source-id base/0"

# Distribute via local NTRIP
docker run --rm --network host ghcr.io/rise-maritime/keelson:0.5.1-pre.2 \
  "keelson2rtcm --realm my-vessel --entity-id gnss | \
   ntrip-cli --port 2101 --mountpoint RTCM3"

# Connect to authenticated NTRIP caster and publish RTCM to Keelson
export NTRIP_USERNAME="<ntrip-username>"
export NTRIP_PASSWORD="<ntrip-password>"

docker run --rm --network host \
  -e NTRIP_USERNAME \
  -e NTRIP_PASSWORD \
  ghcr.io/rise-maritime/keelson:0.5.1-pre.2 \
  "ntrip2keelson \
     --realm rise \
     --entity-id case \
     --source-id ntrip/primary \
     --caster-host nrtk-swepos.lm.se \
     --caster-port 8500 \
     --mountpoint MSM_GNSS \
     --username \"\$NTRIP_USERNAME\" \
     --password-env NTRIP_PASSWORD \
     --position-source-id 'ardusimple/**' \
     --initial-latitude 57.68900200 \
     --initial-longitude 11.97563790 \
     --initial-altitude 62.24"
```

---

## Known limitations

### ntrip-cli

* **No NTRIP authentication.** The local NTRIP v1 server does not support `Authorization` headers. Any client that can reach the server can connect and receive data.
* **No TLS.** RTCM data is transmitted unencrypted. Use a VPN, SSH tunnel, firewall, or private network when required.
* **NTRIP v1 only.** NTRIP v2 and HTTP/1.1 chunked transfer are not supported by `ntrip-cli`.

### ntrip2keelson

* The NTRIP password should preferably be provided through `--password-env` rather than directly on the command line.
* Some NTRIP casters require a valid approximate rover position before they start streaming RTCM. Use `--initial-latitude`, `--initial-longitude`, and `--initial-altitude` if no `location_fix` has been received yet.
* The `--position-source-id` argument expects only the Keelson source-id suffix, for example `ardusimple/**`, not the full Keelson key.
* `/dev/ttyUSB0` should not be used for production serial routing. Prefer `/dev/serial/by-id/...` or a custom udev symlink.
