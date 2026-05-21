# nmea

Bidirectional NMEA0183 and NMEA2000 connectors for Keelson. Provides four binaries for converting between NMEA protocols and Keelson/Zenoh.

## Binaries

- [`nmea01832keelson`](#nmea01832keelson) — Parse NMEA0183 from STDIN, publish to Zenoh
- [`keelson2nmea0183`](#keelson2nmea0183) — Subscribe from Zenoh, output NMEA0183 to STDOUT
- [`n2k2keelson`](#n2k2keelson) — Publish NMEA2000 from a CAN gateway to Zenoh
- [`keelson2n2k`](#keelson2n2k) — Subscribe from Zenoh, inject NMEA2000 into a CAN gateway

## `nmea01832keelson`

Reads NMEA0183 sentences line-by-line from standard input, parses them using pynmea2, and publishes extracted data to Keelson subjects on the Zenoh bus.

Supported sentence types: GGA, RMC, HDT, VTG, ZDA, GLL, ROT, GSA.

```
usage: nmea01832keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                        [--connect CONNECT] [--listen LISTEN] -r REALM -e
                        ENTITY_ID -s SOURCE_ID [--publish-raw]

Parse NMEA0183 sentences from STDIN and publish to Keelson/Zenoh

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -r REALM, --realm REALM
                        Keelson realm (base path) (default: None)
  -e ENTITY_ID, --entity-id ENTITY_ID
                        Entity identifier (default: None)
  -s SOURCE_ID, --source-id SOURCE_ID
                        Source identifier for published data (default: None)
  --publish-raw         Also publish raw NMEA sentences to 'raw' subject (default: False)
```

### Example

```bash
# Read NMEA from a serial GPS and publish to Zenoh
socat /dev/ttyUSB0,b4800 STDOUT | \
  uv run python connectors/nmea/bin/nmea01832keelson.py \
    -r rise -e my_vessel -s gps/0 --publish-raw
```

## `keelson2nmea0183`

Subscribes to Keelson subjects on the Zenoh bus, aggregates data using skarv, and generates NMEA0183 sentences written to standard output.

Generated sentence types: GGA, RMC, HDT, VTG, ZDA, GLL, ROT, GSA.

```
usage: keelson2nmea0183 [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                        [--connect CONNECT] [--listen LISTEN] -r REALM -e
                        ENTITY_ID [--talker-id TALKER_ID]
                        [--source_id_<subject> SOURCE_ID]

Subscribe to Keelson/Zenoh and output NMEA0183 to STDOUT

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -r REALM, --realm REALM
                        Keelson realm (base path) (default: None)
  -e ENTITY_ID, --entity-id ENTITY_ID
                        Entity identifier (default: None)
  --talker-id TALKER_ID
                        NMEA talker ID (e.g., GP, GN, GL) (default: GP)
  --source_id_<subject> SOURCE_ID
                        Source ID pattern for each subject (supports wildcards) (default: **)
```

### Example

```bash
# Subscribe to vessel data and output NMEA0183 to a network port
uv run python connectors/nmea/bin/keelson2nmea0183.py \
  -r rise -e my_vessel --talker-id GP | \
  socat STDIN TCP4-LISTEN:10110,reuseaddr,fork
```

## `n2k2keelson`

Opens a CAN gateway, decodes NMEA2000 frames, and publishes the extracted data to Keelson subjects on the Zenoh bus.

Supported PGNs: 129025 (Position), 129026 (COG & SOG), 129029 (GNSS), 127250 (Heading), 127257 (Attitude), 130306 (Wind), 127245 (Rudder), 130311 (Environmental).

### Gateway profiles

`--gateway` selects a named gateway profile:

| Profile | Transport | Notes |
|---|---|---|
| `yden02` | TCP | Yacht Devices YDEN-02 in RAW mode |
| `ebyte` | TCP | EByte ECAN raw CAN-over-TCP bridge |
| `actisense` | TCP | Generic Actisense N2K-ASCII gateway (receive-only) |
| `waveshare` | USB | WaveShare USB-CAN-A serial gateway |

On connect the connector probes the gateway's identity and appends it to the
`source_id` as `<gateway-type>/<claimed-address>`. For example, `-s n2k/primary`
against a YDEN-02 claiming address 180 publishes under `n2k/primary/yden02/180`;
if the claimed address cannot be determined the type alone is appended
(`n2k/primary/yden02`).

```
usage: n2k2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] [--listen LISTEN] -r REALM -e ENTITY_ID
                   -s SOURCE_ID [--publish-raw]
                   --gateway {actisense,ebyte,waveshare,yden02}
                   [--host HOST] [--port PORT] [--device DEVICE]
                   [--include-pgns INCLUDE_PGNS] [--exclude-pgns EXCLUDE_PGNS]

Publish NMEA2000 data from a CAN gateway to Keelson/Zenoh

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL Logging level (default: INFO)
  --mode {peer,client}, -m {peer,client}
                        The Zenoh session mode.
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447
  -r, --realm REALM     Keelson realm (e.g., 'vessel/sv_colibri')
  -e, --entity-id ENTITY_ID
                        Entity identifier (e.g., 'sensors')
  -s, --source-id SOURCE_ID
                        Base source identifier (e.g., 'n2k/primary'). The probed
                        gateway identity is appended as '<type>/<address>'.
  --publish-raw         Also publish raw NMEA2000 JSON to the 'raw' subject

CAN gateway:
  --gateway {actisense,ebyte,waveshare,yden02}
                        CAN gateway profile to open.
  --host HOST           Gateway host (TCP gateway profiles)
  --port PORT           Gateway TCP port (TCP gateway profiles)
  --device DEVICE       Gateway serial device path (USB gateway profiles)
  --include-pgns INCLUDE_PGNS
                        Comma-separated list of PGNs to include
  --exclude-pgns EXCLUDE_PGNS
                        Comma-separated list of PGNs to exclude
```

### Example

```bash
# Read NMEA2000 from a YDEN-02 over TCP
uv run python connectors/nmea/bin/n2k2keelson.py \
  -r rise -e my_vessel -s n2k/primary \
  --gateway yden02 --host 192.168.4.1 --port 1457
```

## `keelson2n2k`

Subscribes to Keelson subjects on the Zenoh bus, aggregates data using skarv, generates NMEA2000 messages, and injects them into a CAN gateway.

Generated PGNs: 129025, 129026, 129029, 127250, 127257, 130306, 127245, 130311.

`--gateway` selects a named gateway profile (`yden02`, `ebyte`, `actisense`, `waveshare`) — see the [`n2k2keelson` gateway profiles](#gateway-profiles) table. On connect the gateway's identity is probed and logged; note that a *polite* gateway (YDEN-02, Actisense) rewrites the source address of injected frames to its own claimed address, so verify injection on payload-internal markers rather than the source address.

```
usage: keelson2n2k [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] [--listen LISTEN] -r REALM -e ENTITY_ID
                   [--source-address SOURCE_ADDRESS] [--priority PRIORITY]
                   --gateway {actisense,ebyte,waveshare,yden02}
                   [--host HOST] [--port PORT] [--device DEVICE]
                   [--source_id_<subject> SOURCE_ID]

Subscribe to Keelson/Zenoh and inject NMEA2000 into a CAN gateway

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL Logging level (default: INFO)
  --mode {peer,client}, -m {peer,client}
                        The Zenoh session mode.
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447
  -r, --realm REALM     Keelson realm (base path)
  -e, --entity-id ENTITY_ID
                        Entity identifier
  --source-address SOURCE_ADDRESS
                        NMEA2000 source address (0-253). Note: a polite gateway
                        rewrites this to its own claimed address.
  --priority PRIORITY   NMEA2000 message priority (0-7, lower is higher priority)
  --source_id_<subject> SOURCE_ID
                        Source ID pattern for each subject (supports wildcards)

CAN gateway:
  --gateway {actisense,ebyte,waveshare,yden02}
                        CAN gateway profile to inject into.
  --host HOST           Gateway host (TCP gateway profiles)
  --port PORT           Gateway TCP port (TCP gateway profiles)
  --device DEVICE       Gateway serial device path (USB gateway profiles)
```

### Example

```bash
# Inject Keelson data into a YDEN-02 over TCP
uv run python connectors/nmea/bin/keelson2n2k.py \
  -r rise -e my_vessel \
  --gateway yden02 --host 192.168.4.1 --port 1457
```
