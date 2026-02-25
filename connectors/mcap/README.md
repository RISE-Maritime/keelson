# MCAP

Provides CLI tools for MCAP storage file management within Keelson.

**Tools:**

- [MCAP Record](#mcap-record)
- [MCAP Tagg](#mcap-tagg) (Data postprocessing & recovery)
- [MCAP Replay](#mcap-replay)

## MCAP Record

Records envelopes to an MCAP file, injecting the appropriate message schemas for all well-known payloads. Supports time-based and size-based file rotation, as well as SIGHUP-triggered rotation for logrotate compatibility.

### Usage

```
usage: mcap-record [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] [--listen LISTEN] -k KEY
                   --output-folder OUTPUT_FOLDER [--file-name FILE_NAME]
                   [--query | --no-query] [--show-frequencies | --no-show-frequencies]
                   [--extra-subjects-types EXTRA_SUBJECTS_TYPES]
                   [--rotate-when {S,M,H,D,midnight,W0,W1,W2,W3,W4,W5,W6}]
                   [--rotate-interval ROTATE_INTERVAL]
                   [--rotate-size ROTATE_SIZE] [--pid-file PID_FILE]

A pure python mcap recorder for keelson

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -k KEY, --key KEY     Key expressions to subscribe to from the Zenoh session (default: None)
  --output-folder OUTPUT_FOLDER
                        Folder path where recordings will be stored. (default: None)
  --file-name FILE_NAME
                        File name of recording, will be given suffix '.mcap'. Format codes
                        supported by strftime can be used. (default: %Y-%m-%d_%H%M%S)
  --query, --no-query   Query router storage for keys before subscribing to them (default: False)
  --show-frequencies, --no-show-frequencies
                        Show average message frequencies every 10 seconds (default: False)
  --extra-subjects-types EXTRA_SUBJECTS_TYPES
                        Add additional well-known subjects and protobuf types as
                        --extra-subjects-types=path/to/subjects.yaml,path_to_protobuf_file_descriptor_set.bin
                        (default: None)
  --rotate-when {S,M,H,D,midnight,W0,W1,W2,W3,W4,W5,W6}
                        Time-based rotation interval: S=seconds, M=minutes, H=hours, D=days,
                        midnight=at midnight, W0-W6=weekly on day 0-6 (Monday=0). Use with
                        --rotate-interval for multiplier. (default: None)
  --rotate-interval ROTATE_INTERVAL
                        Multiplier for --rotate-when (default: 1)
  --rotate-size ROTATE_SIZE
                        Size-based rotation threshold (e.g., '1GB', '500MB', '100KB'). Rotates
                        when file exceeds this size. (default: None)
  --pid-file PID_FILE   Write PID to this file for logrotate scripts to send SIGHUP signals.
                        (default: None)
```

### Example

```bash
# Record all keys under a realm with hourly rotation
uv run python connectors/mcap/bin/keelson2mcap.py \
  --output-folder ./recordings \
  --file-name "%Y-%m-%d_%H%M%S" \
  --rotate-when H \
  -k "rise/v0/my_vessel/pubsub/**"
```

### Run in container

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "mcap-record -h"

# Record
docker run --rm --network host \
  --volume /home/user/rec_mcap:/rec_mcap \
  ghcr.io/rise-maritime/keelson \
  "mcap-record --output-folder /rec_mcap -k rise/v0/my_vessel/pubsub/**"
```

## MCAP Tagg

Post-processing tool that recovers and processes MCAP files. Runs the `mcap recover` command on all `.mcap` files in a given directory.

### Usage

```
usage: mcap-tagg [-h] [--log-level LOG_LEVEL] -id INPUT_DIR [-od OUTPUT_DIR]

A pure python mcap post processing tool with annotation capabilities

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Log level 10=DEBUG, 20=INFO, 30=WARNING (default: 20)
  -id INPUT_DIR, --input-dir INPUT_DIR
                        The directory containing the files to be processed (files must be in
                        .mcap format) (default: None)
  -od OUTPUT_DIR, --output-dir OUTPUT_DIR
                        The directory to save the processed files (default is the input
                        directory) (default: None)
```

## MCAP Replay

Replays all messages from a recorded MCAP file.

### Usage

```
usage: mcap-replay [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] [--listen LISTEN] [--loop]
                   [--replay-key-tag] -mf MCAP_FILE [-ts TIME_START]
                   [-te TIME_END] [-rk REPLAY_KEY]

A pure python mcap replayer for keelson

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  --loop                Loop the replay forever (default: False)
  --replay-key-tag      appending replay tag to key expression (default: False)
  -mf MCAP_FILE, --mcap-file MCAP_FILE
                        File path to read recorded data from (default: None)
  -ts TIME_START, --time-start TIME_START
                        Replay start time in string format yyyy-mm-ddTHH:MM:SS to start
                        replaying (default: None)
  -te TIME_END, --time-end TIME_END
                        Replay end time in string format yyyy-mm-ddTHH:MM:SS to stop
                        replaying (default: None)
  -rk REPLAY_KEY, --replay-key REPLAY_KEY
                        Replay only messages with the given key expression set multiple times
                        for multiple keys (default: None)
```

### Run in container

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "mcap-replay -h"

# Replay
docker run --rm --network host \
  --volume /home/user/rec:/rec \
  ghcr.io/rise-maritime/keelson \
  "mcap-replay --mcap-file /rec/2024-05-15.mcap"
```

### Debug or run within dev-container

```sh
# Single run
uv run python connectors/mcap/bin/mcap2keelson.py --log-level 20 --mcap-file ./recording.mcap

# Loop forever
uv run python connectors/mcap/bin/mcap2keelson.py --log-level 20 --loop --mcap-file ./recording.mcap

# Time range boundaries
uv run python connectors/mcap/bin/mcap2keelson.py --log-level 20 --mcap-file ./recording.mcap \
  --time-start 2024-09-06T08:47:00 --time-end 2024-09-06T08:48:00

# Topic boundaries
uv run python connectors/mcap/bin/mcap2keelson.py --log-level 20 --mcap-file ./recording.mcap \
  --replay-key rise/v0/my_vessel/pubsub/point_cloud/1201
```
