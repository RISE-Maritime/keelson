# MCAP

Provides an interface cli tool for the mcap storage file management within Keelson

**Tools:**

- [MCAP Record](#mcap-recorder)
- [MCAP Tagg](#mcap-tagg) (Data postprocessing & annotation)
- [MCAP Replay](#mcap-replay)  

## [MCAP-Recorder](./bin/mcap-record)

Records envelopes to an mcap file injecting the appropriate message schemas for all well-known payloads.

### Recorder file naming modes

Manual use "--output" ex. "--output rec_mcap/2024-05-15.mcap"

Automatic use "--output_path" ex. "--output rec_mcap" will write file "rec_mcap/2024-05-15_0930.mcap"

```bash
# Show help 
docker run ghcr.io/mo-rise/keelson:0.3.4 "mcap-record -h"

# Record
# Crete an recording folder before running, for this example "mkdir rec_mcap" 
docker run --network host --volume /home/user/rec_mcap:/rec_mcap ghcr.io/mo-rise/keelson:0.3.4 "mcap-record --output rec_mcap/2024-05-15.mcap -k rise/v0/masslab/pubsub/**" -k new/key
```

## MCAP-Tagg

```bash
python3 connec
```


## [MCAP-Replay](./bin/mcap-replay)

Replays all messages from a recorded mcap file

### Usage

```sh
usage: mcap-replay [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT] [--loop] [--replay-key-tag] -mf MCAP_FILE [-ts TIME_START] [-te TIME_END] [-rk REPLAY_KEY]

A pure python mcap replayer for keelson

options:
  -h, --help            show this help message and exit

  --log-level LOG_LEVEL

  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  
  --connect CONNECT     Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447 (default: None)
  
  --loop                Loop the replay forever (default: False)
  
  --replay-key-tag      appending replay tag to key expression (default: False)
  
  -mf MCAP_FILE, --mcap-file MCAP_FILE
                        File path to read recorded data from (default: None)
  
  -ts TIME_START, --time-start TIME_START
                        Replay start time in string format yyyy-mm-ddTHH:MM:SS to start replaying (default: None)
  
  -te TIME_END, --time-end TIME_END
                        Replay end time in in string format yyyy-mm-ddTHH:MM:SS to stop replaying (default: None)
  
  -rk REPLAY_KEY, --replay-key REPLAY_KEY
                        Replay only messages with the given key expression set multiple times for multiple keys (default: None)
```

### Run in container

```bash
# Show help 
docker run --rm ghcr.io/mo-rise/keelson:0.3.4 "mcap-replay -h"

# Record
docker run --rm --network host --volume /home/user/rec:/rec ghcr.io/rise-maritime/keelson:0.3.4 "mcap-replay --mcap-file rec/2024-05-15.mcap"
```

### Debug or Run within dev-container

```sh
# Single run
python3 connectors/mcap/bin/mcap-replay --log-level 20 --mcap-file ./0846_radar_cam.mcap
python3 connectors/mcap/bin/mcap-record --log-level 10 --output test.mcap -k rise/v0/sjofartsverket/pubsub/**

# Loop forever 
python3 connectors/mcap/bin/mcap-replay --log-level 20 --loop --mcap-file ./0846_radar_cam.mcap

# Time range boundaries 
python3 connectors/mcap/bin/mcap-replay --log-level 20 --mcap-file ./0846_radar_cam.mcap --time-start 2024-09-06T08:47:00 --time-end 2024-09-06T08:48:00

# Topic boundaries 
python3 connectors/mcap/bin/mcap-replay --log-level 20 --mcap-file ./0846_radar_cam.mcap --replay-key rise/v0/landkrabba/pubsub/point_cloud/1201
```

