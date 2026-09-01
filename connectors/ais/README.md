# keelson-connector-ais

Multiple co-hosted connectors towards AIS data flows. Now part of the [keelson monorepo](https://github.com/RISE-Maritime/keelson).

* `ais2keelson` - reads binary AIS messages encoded in NMEA0183 sentences from STDIN and puts to zenoh
* `digitraffic2keelson` - reads JSON encoded AIS from the digitraffic mqtt websocket api and puts to zenoh
* `keelson2ais` - reads data from zenoh (adhering to the keelson protocol) and outputs AIS encoded NMEA0183 messages to stdout

## AIS Sentinel Value Filtering

AIS messages use special sentinel values to indicate "not available" data. The connectors filter these values and will **not** publish a subject when the corresponding sentinel is detected:

| Subject | AIS Sentinel Value | Meaning |
|---|---|---|
| `heading_true_north_deg` | `511` | Heading not available |
| `course_over_ground_deg` | `360.0` | COG not available |
| `speed_over_ground_knots` | `102.3` | SOG not available |
| `yaw_rate_degps` | `+/-128` | Rate of turn not available |

## Usage

### `ais2keelson`
```
usage: ais2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                   -s SOURCE_ID [--publish-raw] [--publish-json] [--publish-fields]

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
  --publish-raw
  --publish-json
  --publish-fields
```

### `digitraffic2keelson`
```
usage: digitraffic2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                           [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                           -s SOURCE_ID [--publish-raw] [--publish-fields]

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
  --publish-raw
  --publish-fields
```

### `keelson2ais`
```
usage: keelson2ais [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                   [--talker-id TALKER_ID] [--radio-channel RADIO_CHANNEL]
                   [--msg1-at-most-every MSG1_AT_MOST_EVERY] [--msg5-period MSG5_PERIOD]
                   [--source_id_location_fix SOURCE_ID_LOCATION_FIX]
                   [--source_id_yaw_rate_degps SOURCE_ID_YAW_RATE_DEGPS]
                   [--source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG]
                   [--source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG]
                   [--source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS]
                   [--source_id_mmsi_number SOURCE_ID_MMSI_NUMBER]
                   [--source_id_draught_mean_m SOURCE_ID_DRAUGHT_MEAN_M]
                   [--source_id_length_over_all_m SOURCE_ID_LENGTH_OVER_ALL_M]
                   [--source_id_breadth_over_all_m SOURCE_ID_BREADTH_OVER_ALL_M]
                   [--source_id_name SOURCE_ID_NAME] [--source_id_call_sign SOURCE_ID_CALL_SIGN]
                   [--source_id_imo_number SOURCE_ID_IMO_NUMBER]

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
  --talker-id TALKER_ID
  --radio-channel RADIO_CHANNEL
  --msg1-at-most-every MSG1_AT_MOST_EVERY
                        Throttle AIS Message 1 to be sent at most once every N seconds (e.g., 1.0
                        for at most once per second). Default 0.0 means no throttling. (default:
                        0.0)
  --msg5-period MSG5_PERIOD
                        Periodic interval for AIS Message 5 in seconds. (default: 300)
  --source_id_location_fix SOURCE_ID_LOCATION_FIX
  --source_id_yaw_rate_degps SOURCE_ID_YAW_RATE_DEGPS
  --source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG
  --source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG
  --source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS
  --source_id_mmsi_number SOURCE_ID_MMSI_NUMBER
  --source_id_draught_mean_m SOURCE_ID_DRAUGHT_MEAN_M
  --source_id_length_over_all_m SOURCE_ID_LENGTH_OVER_ALL_M
  --source_id_breadth_over_all_m SOURCE_ID_BREADTH_OVER_ALL_M
  --source_id_name SOURCE_ID_NAME
  --source_id_call_sign SOURCE_ID_CALL_SIGN
  --source_id_imo_number SOURCE_ID_IMO_NUMBER
```

### docker-compose example setup
```yaml
services:

  source-onboard-transponder:
    image: ghcr.io/rise-maritime/keelson
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "socat TCP4-CONNECT:<IP>:<PORT> STDOUT | ais2keelson -r <realm> -e <entity> -s <source> --publish-raw --publish-fields"
      ]

  source-digitraffic:
    image: ghcr.io/rise-maritime/keelson
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "digitraffic2keelson -r <realm> -e <entity> -s digitraffic --publish-raw --publish-fields"
      ]

  sink-keelson2ais:
    image: ghcr.io/rise-maritime/keelson
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "keelson2ais -r <realm> -e <entity> --talker-id AIVDO --radio-channel A | socat STDIN TCP4:<IP>:<PORT>"
      ]
```
