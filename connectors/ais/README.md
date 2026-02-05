# keelson-connector-ais

Multiple co-hosted connectors towards AIS data flows. Now part of the [keelson monorepo](https://github.com/RISE-Maritime/keelson).

* `ais2keelson` - reads binary AIS messages encoded in NMEA0183 sentences from STDIN and puts to zenoh
* `digitraffic2keelson` - reads JSON encoded AIS from the digitraffic mqtt websocket api and puts to zenoh
* `keelson2ais` - reads data from zenoh (adhering to the keelson protocol) and outputs AIS encoded NMEA0183 messages to stdout

## Usage

### `ais2keelson`
```
usage: ais2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] -r REALM -e ENTITY_ID -s SOURCE_ID
                   [--publish-raw] [--publish-json] [--publish-fields]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working.
                        ex. tcp/localhost:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --publish-raw
  --publish-json
  --publish-fields
```

### `digitraffic2keelson`
```
usage: digitraffic2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                           [--connect CONNECT] -r REALM -e ENTITY_ID -s
                           SOURCE_ID [--publish-raw] [--publish-fields]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working.
                        ex. tcp/localhost:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --publish-raw
  --publish-fields
```

### `keelson2ais`
```
usage: keelson2ais [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] -r REALM -e ENTITY_ID
                   [--talker-id TALKER_ID] [--radio-channel RADIO_CHANNEL]
                   [--source_id_* SOURCE_ID_*]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to, in case multicast is not working.
                        ex. tcp/localhost:7447 (default: None)
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
