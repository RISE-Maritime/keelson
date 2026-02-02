# keelson-connector-ais

Multiple co-hosted connectors towards ais data flows:

* `ais2keelson` - reads binary AIS messages encoded in NMEA0183 sentences from STDIN and puts to zenoh
* `digitraffic2keelson` - reads JSON encoded AIS from the digitraffic mqtt websocket api and puts to zenoh
* `keelson2ais` - reads data from zenoh (adhering to the keelson protocol) and outputs AIS encoded NMEA0183 messages to stdout

Packaged as a docker container available from: https://github.com/RISE-Maritime/keelson-connector-ais/pkgs/container/keelson-connector-ais. NOTE: This container has [porla](https://github.com/RISE-Maritime/porla) as its base container and thus also includes all the binaries provided by porla.

## Usage

### `ais2keelson`
```
usage: ais2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT] -r REALM -e ENTITY_ID -s SOURCE_ID [--publish-raw] [--publish-json] [--publish-fields]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --publish-raw
  --publish-json
  --publish-fields
```

### `digitraffic2keelson`
```
usage: digitraffic2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT] -r REALM -e ENTITY_ID -s SOURCE_ID [--publish-raw] [--publish-fields]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --publish-raw
  --publish-fields
```

### `keelson2ais`
```
usage: keelson2ais [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT] -r REALM -e ENTITY_ID [--talker-id TALKER_ID] [--radio-channel RADIO_CHANNEL]
                   [--source_id_location_fix SOURCE_ID_LOCATION_FIX] [--source_id_rate_of_turn_degpm SOURCE_ID_RATE_OF_TURN_DEGPM]
                   [--source_id_heading_true_north_deg SOURCE_ID_HEADING_TRUE_NORTH_DEG] [--source_id_course_over_ground_deg SOURCE_ID_COURSE_OVER_GROUND_DEG]
                   [--source_id_speed_over_ground_knots SOURCE_ID_SPEED_OVER_GROUND_KNOTS] [--source_id_vessel_mmsi_number SOURCE_ID_VESSEL_MMSI_NUMBER]
                   [--source_id_draught_mean_m SOURCE_ID_DRAUGHT_MEAN_M] [--source_id_length_over_all_m SOURCE_ID_LENGTH_OVER_ALL_M]
                   [--source_id_breadth_over_all_m SOURCE_ID_BREADTH_OVER_ALL_M] [--source_id_vessel_name SOURCE_ID_VESSEL_NAME]
                   [--source_id_vessel_call_sign SOURCE_ID_VESSEL_CALL_SIGN] [--source_id_vessel_imo_number SOURCE_ID_VESSEL_IMO_NUMBER]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  --talker-id TALKER_ID
                        (default: AIVDO)
  --radio-channel RADIO_CHANNEL
                        (default: A)
  --source_id_* SOURCE_ID_*
                        Source ID filters for each subject (default: **)
```

### docker-compose example setup
```yaml
services:

  source-onboard-transponder:
    image: ghcr.io/rise-maritime/keelson-connector-ais:v0.1.8
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "socat TCP4-CONNECT:<IP>:<PORT> STDOUT | ais2keelson -r <realm> -e <entity> -s <source> --publish-raw --publish-fields"
      ]

  source-digitraffic:
    image: ghcr.io/rise-maritime/keelson-connector-ais:v0.1.8
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "digitraffic2keelson -r <realm> -e <entity> -s digitraffic --publish-raw --publish-fields"
      ]

  sink-keelson2ais:
    image: ghcr.io/rise-maritime/keelson-connector-ais:v0.1.8
    restart: unless-stopped
    network_mode: "host"
    command:
      [
        "keelson2ais -r <realm> -e <entity> --talker-id AIVDO --radio-channel A | socat STDIN TCP4:<IP>:<PORT>"
      ]
```
