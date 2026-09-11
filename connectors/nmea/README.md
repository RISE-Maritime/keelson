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
usage: nmea01832keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                        [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                        -s SOURCE_ID [--publish-raw]

Parse NMEA0183 sentences from STDIN and publish to Keelson/Zenoh

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
  -r, --realm REALM     Keelson realm (base path) (default: None)
  -e, --entity-id ENTITY_ID
                        Entity identifier (default: None)
  -s, --source-id SOURCE_ID
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

### NGX-1 in NMEA 0183 Convert mode

An Actisense NGX-1-USB ships in NMEA 0183 Convert mode at 4800 baud. In that
mode it emits NMEA 0183 directly, which `nmea01832keelson` consumes — no device
reconfiguration needed:

```yaml
# docker-compose service: NGX-1 (Convert mode) as a 0183 source
nmea0183_listener_ngx1:
  image: ghcr.io/rise-maritime/keelson:latest
  devices:
    - /dev/ttyUSB0:/dev/ttyUSB0
  command:
    - >-
      stty -F /dev/ttyUSB0 4800 raw -echo &&
      cat /dev/ttyUSB0 |
      nmea01832keelson --mode client --connect tcp/127.0.0.1:7447
      -r rise -e nmeaboard -s ngx1_0183
```

**0183 vs raw NMEA 2000.** The same NGX-1 can instead be read as raw NMEA 2000
with `n2k2keelson --gateway actisense_ngx1` (see [`n2k2keelson`](#n2k2keelson)):

| | 0183 Convert mode | Raw N2K (`actisense_ngx1`) |
|---|---|---|
| Data | Lossy — the 0183 subset (position, COG/SOG, heading, wind, …) | Full — every PGN the connector decodes |
| Device setup | Factory default; nothing to configure | Connector auto-switches the device to Transfer Receive All mode |
| Interop | Plug-compatible with chartplotters, OpenCPN, SignalK | Keelson only |

The two are mutually exclusive on one device — a USB NGX-1 is in a single mode
at a time, and `--gateway actisense_ngx1` actively switches it *out* of Convert
mode. Choose 0183 when the device must also feed 0183 consumers, or the 0183
subset is sufficient; choose raw N2K for full-fidelity NMEA 2000.

## `keelson2nmea0183`

Subscribes to Keelson subjects on the Zenoh bus, aggregates data using skarv, and generates NMEA0183 sentences written to standard output.

Generated sentence types: GGA, RMC, HDT, VTG, ZDA, GLL, ROT, GSA.

```
usage: keelson2nmea0183 [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                        [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                        [--talker-id TALKER_ID] [--source_id_location_fix SOURCE_ID_LOCATION_FIX]
                        [--source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS]
                        [--source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG]
                        [--source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG]
                        [--source_id_yaw_rate_degps SOURCE_ID_YAW_RATE_DEGPS]
                        [--source_id_location_fix_hdop SOURCE_ID_LOCATION_FIX_HDOP]
                        [--source_id_location_fix_vdop SOURCE_ID_LOCATION_FIX_VDOP]
                        [--source_id_location_fix_pdop SOURCE_ID_LOCATION_FIX_PDOP]
                        [--source_id_location_fix_satellites_used SOURCE_ID_LOCATION_FIX_SATELLITES_USED]
                        [--source_id_location_fix_undulation_m SOURCE_ID_LOCATION_FIX_UNDULATION_M]
                        [--source_id_location_fix_quality SOURCE_ID_LOCATION_FIX_QUALITY]

Subscribe to Keelson/Zenoh and output NMEA0183 to STDOUT

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
  -r, --realm REALM     Keelson realm (base path) (default: None)
  -e, --entity-id ENTITY_ID
                        Entity identifier (default: None)
  --talker-id TALKER_ID
                        NMEA talker ID (e.g., GP, GN, GL) (default: GP)
  --source_id_location_fix SOURCE_ID_LOCATION_FIX
                        Source ID pattern for location_fix (supports wildcards) (default: **)
  --source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS
                        Source ID pattern for speed_over_ground_knots (supports wildcards)
                        (default: **)
  --source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG
                        Source ID pattern for course_over_ground_deg (supports wildcards)
                        (default: **)
  --source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG
                        Source ID pattern for heading_true_north_deg (supports wildcards)
                        (default: **)
  --source_id_yaw_rate_degps SOURCE_ID_YAW_RATE_DEGPS
                        Source ID pattern for yaw_rate_degps (supports wildcards) (default: **)
  --source_id_location_fix_hdop SOURCE_ID_LOCATION_FIX_HDOP
                        Source ID pattern for location_fix_hdop (supports wildcards) (default: **)
  --source_id_location_fix_vdop SOURCE_ID_LOCATION_FIX_VDOP
                        Source ID pattern for location_fix_vdop (supports wildcards) (default: **)
  --source_id_location_fix_pdop SOURCE_ID_LOCATION_FIX_PDOP
                        Source ID pattern for location_fix_pdop (supports wildcards) (default: **)
  --source_id_location_fix_satellites_used SOURCE_ID_LOCATION_FIX_SATELLITES_USED
                        Source ID pattern for location_fix_satellites_used (supports wildcards)
                        (default: **)
  --source_id_location_fix_undulation_m SOURCE_ID_LOCATION_FIX_UNDULATION_M
                        Source ID pattern for location_fix_undulation_m (supports wildcards)
                        (default: **)
  --source_id_location_fix_quality SOURCE_ID_LOCATION_FIX_QUALITY
                        Source ID pattern for location_fix_quality (supports wildcards) (default:
                        **)
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

Supported PGNs: 129025 (Position), 129026 (COG & SOG), 129029 (GNSS), 127250 (Heading), 127257 (Attitude), 130306 (Wind), 127245 (Rudder), 130311 (Environmental), 129038 (AIS Class A position), 129039 (AIS Class B position), 129794 (AIS Class A static & voyage).

AIS reports (129038/129039/129794) are published per observed vessel, scoped with the `target_id` `mmsi_<MMSI>`.

### Gateway profiles

`--gateway` selects a named gateway profile:

| Profile | Transport | Notes |
|---|---|---|
| `yden02` | TCP | Yacht Devices YDEN-02 in RAW mode |
| `ebyte` | TCP | EByte ECAN raw CAN-over-TCP bridge |
| `actisense` | TCP | Generic Actisense N2K-ASCII gateway (receive-only) |
| `waveshare` | USB | WaveShare USB-CAN-A serial gateway |
| `actisense_ngx1` | USB | Actisense NGX-1-USB; auto-switched into Transfer Receive All mode on connect |

On connect the connector probes the gateway's identity and appends it to the
`source_id` as `<gateway-type>/<claimed-address>`. For example, `-s n2k/primary`
against a YDEN-02 claiming address 180 publishes under `n2k/primary/yden02/180`;
if the claimed address cannot be determined the type alone is appended
(`n2k/primary/yden02`).

The `actisense_ngx1` profile runs a connect-time BST-BEM pre-flight: it probes
the NGX-1's operating mode and, if the device is still in its factory NMEA 0183
Convert mode, switches it into Transfer Receive All mode at `--ensure-baud`
(default 115200). The change is non-persistent unless `--persist` is given.

```
usage: n2k2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                   -s SOURCE_ID [--publish-raw]
                   --gateway {actisense,actisense_ngx1,ebyte,waveshare,yden02} [--host HOST]
                   [--port PORT] [--device DEVICE] [--include-pgns INCLUDE_PGNS]
                   [--exclude-pgns EXCLUDE_PGNS] [--ensure-baud ENSURE_BAUD] [--persist]

Publish NMEA2000 data from a CAN gateway to Keelson/Zenoh

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
  -r, --realm REALM     Keelson realm (e.g., 'vessel/sv_colibri') (default: None)
  -e, --entity-id ENTITY_ID
                        Entity identifier (e.g., 'sensors') (default: None)
  -s, --source-id SOURCE_ID
                        Base source identifier (e.g., 'n2k/primary'). The probed gateway identity
                        is appended as '<type>/<address>'. (default: None)
  --publish-raw         Also publish raw NMEA2000 JSON to the 'raw' subject (default: False)

CAN gateway:
  --gateway {actisense,actisense_ngx1,ebyte,waveshare,yden02}
                        CAN gateway profile to open. (default: None)
  --host HOST           Gateway host (TCP gateway profiles) (default: None)
  --port PORT           Gateway TCP port (TCP gateway profiles) (default: None)
  --device DEVICE       Gateway serial device path (USB gateway profiles) (default: None)
  --include-pgns INCLUDE_PGNS
                        Comma-separated list of PGNs to include (default: None)
  --exclude-pgns EXCLUDE_PGNS
                        Comma-separated list of PGNs to exclude (default: None)
  --ensure-baud ENSURE_BAUD
                        NGX-1 target serial baud rate (actisense_ngx1 only) (default: 115200)
  --persist             Persist NGX-1 configuration to EEPROM (actisense_ngx1 only) (default:
                        False)
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

Generated PGNs: 129025, 129026, 129029, 127250, 127257, 130306, 127245, 130311. With `--inject-as`, also 129038 / 129794 (AIS).

`--gateway` selects a named gateway profile (`yden02`, `ebyte`, `actisense`, `waveshare`, `actisense_ngx1`) — see the [`n2k2keelson` gateway profiles](#gateway-profiles) table. On connect the gateway's identity is probed and logged; note that a *polite* gateway (YDEN-02, Actisense) rewrites the source address of injected frames to its own claimed address, so verify injection on payload-internal markers rather than the source address.

### AIS injection (`--inject-as`)

`keelson2n2k` reads exactly one vessel off the bus. `--inject-as` chooses how that vessel is rendered onto the N2K bus:

| `--inject-as` | Injects | Use |
|---|---|---|
| `ownship` *(default)* | the 8 general instrument PGNs | the vessel **is** the bus's own ship — today's behavior |
| `ownship-ais` | general PGNs **+** AIS 129038/129794 | the own ship, plus its AIS transponder broadcast |
| `ais-target` | AIS 129038/129794 **only** | the vessel appears as an AIS **contact**; no general PGNs |

The AIS modes need the own-ship `mmsi_number` subject; AIS PGNs are skipped (with a warning) while it is absent. 129038 is emitted on `location_fix` updates; 129794 (static & voyage data) is re-sent every `--ais-static-period` seconds. Only Class A AIS is supported — every injected target renders as Class A.

```
usage: keelson2n2k [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                   [--source-address SOURCE_ADDRESS] [--priority PRIORITY]
                   [--inject-as {ownship,ownship-ais,ais-target}]
                   [--ais-static-period AIS_STATIC_PERIOD]
                   --gateway {actisense,actisense_ngx1,ebyte,waveshare,yden02} [--host HOST]
                   [--port PORT] [--device DEVICE] [--ensure-baud ENSURE_BAUD] [--persist]
                   [--source_id_location_fix SOURCE_ID_LOCATION_FIX]
                   [--source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS]
                   [--source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG]
                   [--source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG]
                   [--source_id_heading_magnetic_deg SOURCE_ID_HEADING_MAGNETIC_DEG]
                   [--source_id_yaw_deg SOURCE_ID_YAW_DEG]
                   [--source_id_pitch_deg SOURCE_ID_PITCH_DEG]
                   [--source_id_roll_deg SOURCE_ID_ROLL_DEG]
                   [--source_id_yaw_rate_degps SOURCE_ID_YAW_RATE_DEGPS]
                   [--source_id_location_fix_hdop SOURCE_ID_LOCATION_FIX_HDOP]
                   [--source_id_location_fix_satellites_used SOURCE_ID_LOCATION_FIX_SATELLITES_USED]
                   [--source_id_location_fix_undulation_m SOURCE_ID_LOCATION_FIX_UNDULATION_M]
                   [--source_id_location_fix_quality SOURCE_ID_LOCATION_FIX_QUALITY]
                   [--source_id_apparent_wind_speed_mps SOURCE_ID_APPARENT_WIND_SPEED_MPS]
                   [--source_id_apparent_wind_angle_deg SOURCE_ID_APPARENT_WIND_ANGLE_DEG]
                   [--source_id_true_wind_speed_mps SOURCE_ID_TRUE_WIND_SPEED_MPS]
                   [--source_id_true_wind_angle_deg SOURCE_ID_TRUE_WIND_ANGLE_DEG]
                   [--source_id_rudder_angle_deg SOURCE_ID_RUDDER_ANGLE_DEG]
                   [--source_id_water_temperature_celsius SOURCE_ID_WATER_TEMPERATURE_CELSIUS]
                   [--source_id_air_pressure_pa SOURCE_ID_AIR_PRESSURE_PA]
                   [--source_id_mmsi_number SOURCE_ID_MMSI_NUMBER]
                   [--source_id_nav_status SOURCE_ID_NAV_STATUS] [--source_id_name SOURCE_ID_NAME]
                   [--source_id_call_sign SOURCE_ID_CALL_SIGN]
                   [--source_id_imo_number SOURCE_ID_IMO_NUMBER]
                   [--source_id_vessel_type SOURCE_ID_VESSEL_TYPE]
                   [--source_id_destination SOURCE_ID_DESTINATION] [--source_id_eta SOURCE_ID_ETA]
                   [--source_id_length_over_all_m SOURCE_ID_LENGTH_OVER_ALL_M]
                   [--source_id_breadth_over_all_m SOURCE_ID_BREADTH_OVER_ALL_M]
                   [--source_id_draught_mean_m SOURCE_ID_DRAUGHT_MEAN_M]

Subscribe to Keelson/Zenoh and inject NMEA2000 into a CAN gateway

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
  -r, --realm REALM     Keelson realm (base path) (default: None)
  -e, --entity-id ENTITY_ID
                        Entity identifier (default: None)
  --source-address SOURCE_ADDRESS
                        NMEA2000 source address (0-253). Note: a polite gateway rewrites this to
                        its own claimed address. (default: 1)
  --priority PRIORITY   NMEA2000 message priority (0-7, lower is higher priority) (default: 2)
  --source_id_location_fix SOURCE_ID_LOCATION_FIX
                        Source ID pattern for location_fix (supports wildcards) (default: **)
  --source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS
                        Source ID pattern for speed_over_ground_knots (supports wildcards)
                        (default: **)
  --source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG
                        Source ID pattern for course_over_ground_deg (supports wildcards)
                        (default: **)
  --source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG
                        Source ID pattern for heading_true_north_deg (supports wildcards)
                        (default: **)
  --source_id_heading_magnetic_deg SOURCE_ID_HEADING_MAGNETIC_DEG
                        Source ID pattern for heading_magnetic_deg (supports wildcards) (default:
                        **)
  --source_id_yaw_deg SOURCE_ID_YAW_DEG
                        Source ID pattern for yaw_deg (supports wildcards) (default: **)
  --source_id_pitch_deg SOURCE_ID_PITCH_DEG
                        Source ID pattern for pitch_deg (supports wildcards) (default: **)
  --source_id_roll_deg SOURCE_ID_ROLL_DEG
                        Source ID pattern for roll_deg (supports wildcards) (default: **)
  --source_id_yaw_rate_degps SOURCE_ID_YAW_RATE_DEGPS
                        Source ID pattern for yaw_rate_degps (supports wildcards) (default: **)
  --source_id_location_fix_hdop SOURCE_ID_LOCATION_FIX_HDOP
                        Source ID pattern for location_fix_hdop (supports wildcards) (default: **)
  --source_id_location_fix_satellites_used SOURCE_ID_LOCATION_FIX_SATELLITES_USED
                        Source ID pattern for location_fix_satellites_used (supports wildcards)
                        (default: **)
  --source_id_location_fix_undulation_m SOURCE_ID_LOCATION_FIX_UNDULATION_M
                        Source ID pattern for location_fix_undulation_m (supports wildcards)
                        (default: **)
  --source_id_location_fix_quality SOURCE_ID_LOCATION_FIX_QUALITY
                        Source ID pattern for location_fix_quality (supports wildcards) (default:
                        **)
  --source_id_apparent_wind_speed_mps SOURCE_ID_APPARENT_WIND_SPEED_MPS
                        Source ID pattern for apparent_wind_speed_mps (supports wildcards)
                        (default: **)
  --source_id_apparent_wind_angle_deg SOURCE_ID_APPARENT_WIND_ANGLE_DEG
                        Source ID pattern for apparent_wind_angle_deg (supports wildcards)
                        (default: **)
  --source_id_true_wind_speed_mps SOURCE_ID_TRUE_WIND_SPEED_MPS
                        Source ID pattern for true_wind_speed_mps (supports wildcards) (default:
                        **)
  --source_id_true_wind_angle_deg SOURCE_ID_TRUE_WIND_ANGLE_DEG
                        Source ID pattern for true_wind_angle_deg (supports wildcards) (default:
                        **)
  --source_id_rudder_angle_deg SOURCE_ID_RUDDER_ANGLE_DEG
                        Source ID pattern for rudder_angle_deg (supports wildcards) (default: **)
  --source_id_water_temperature_celsius SOURCE_ID_WATER_TEMPERATURE_CELSIUS
                        Source ID pattern for water_temperature_celsius (supports wildcards)
                        (default: **)
  --source_id_air_pressure_pa SOURCE_ID_AIR_PRESSURE_PA
                        Source ID pattern for air_pressure_pa (supports wildcards) (default: **)
  --source_id_mmsi_number SOURCE_ID_MMSI_NUMBER
                        Source ID pattern for mmsi_number (supports wildcards) (default: **)
  --source_id_nav_status SOURCE_ID_NAV_STATUS
                        Source ID pattern for nav_status (supports wildcards) (default: **)
  --source_id_name SOURCE_ID_NAME
                        Source ID pattern for name (supports wildcards) (default: **)
  --source_id_call_sign SOURCE_ID_CALL_SIGN
                        Source ID pattern for call_sign (supports wildcards) (default: **)
  --source_id_imo_number SOURCE_ID_IMO_NUMBER
                        Source ID pattern for imo_number (supports wildcards) (default: **)
  --source_id_vessel_type SOURCE_ID_VESSEL_TYPE
                        Source ID pattern for vessel_type (supports wildcards) (default: **)
  --source_id_destination SOURCE_ID_DESTINATION
                        Source ID pattern for destination (supports wildcards) (default: **)
  --source_id_eta SOURCE_ID_ETA
                        Source ID pattern for eta (supports wildcards) (default: **)
  --source_id_length_over_all_m SOURCE_ID_LENGTH_OVER_ALL_M
                        Source ID pattern for length_over_all_m (supports wildcards) (default: **)
  --source_id_breadth_over_all_m SOURCE_ID_BREADTH_OVER_ALL_M
                        Source ID pattern for breadth_over_all_m (supports wildcards) (default:
                        **)
  --source_id_draught_mean_m SOURCE_ID_DRAUGHT_MEAN_M
                        Source ID pattern for draught_mean_m (supports wildcards) (default: **)

NMEA 2000 output:
  --inject-as {ownship,ownship-ais,ais-target}
                        What to inject for the vessel read off the bus: 'ownship' = the 8 general
                        instrument PGNs (default); 'ownship-ais' = those plus the vessel's own AIS
                        report (PGN 129038 + 129794); 'ais-target' = only the AIS report, so the
                        vessel appears as an AIS contact and no general PGNs are injected.
                        (default: ownship)
  --ais-static-period AIS_STATIC_PERIOD
                        Seconds between PGN 129794 (AIS static & voyage) emissions (ownship-ais /
                        ais-target modes). (default: 300.0)

CAN gateway:
  --gateway {actisense,actisense_ngx1,ebyte,waveshare,yden02}
                        CAN gateway profile to inject into. (default: None)
  --host HOST           Gateway host (TCP gateway profiles) (default: None)
  --port PORT           Gateway TCP port (TCP gateway profiles) (default: None)
  --device DEVICE       Gateway serial device path (USB gateway profiles) (default: None)
  --ensure-baud ENSURE_BAUD
                        NGX-1 target serial baud rate (actisense_ngx1 only) (default: 115200)
  --persist             Persist NGX-1 configuration to EEPROM (actisense_ngx1 only) (default:
                        False)
```

### Example

```bash
# Inject Keelson data into a YDEN-02 over TCP
uv run python connectors/nmea/bin/keelson2n2k.py \
  -r rise -e my_vessel \
  --gateway yden02 --host 192.168.4.1 --port 1457

# Own-ship nav data plus the vessel's own AIS report
uv run python connectors/nmea/bin/keelson2n2k.py \
  -r rise -e my_vessel \
  --gateway yden02 --host 192.168.4.1 --port 1457 \
  --inject-as ownship-ais

# Inject another vessel so it shows up as an AIS contact
uv run python connectors/nmea/bin/keelson2n2k.py \
  -r rise -e other_vessel \
  --gateway yden02 --host 192.168.4.1 --port 1457 \
  --inject-as ais-target
```
