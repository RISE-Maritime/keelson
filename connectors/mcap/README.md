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
usage: keelson2mcap [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
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
docker run --rm ghcr.io/rise-maritime/keelson "keelson2mcap -h"

# Record
docker run --rm --network host \
  --volume /home/user/rec_mcap:/rec_mcap \
  ghcr.io/rise-maritime/keelson \
  "keelson2mcap --output-folder /rec_mcap -k rise/v0/my_vessel/pubsub/**"
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

Stateful MCAP replay daemon. Walks messages from a recorded MCAP file onto
their original Zenoh topics, with timing preserved. Exposes the
`McapReplayControl` Zenoh RPC service ([interfaces/McapReplayControl.proto](../../interfaces/McapReplayControl.proto))
for live control (play/pause/seek/load/etc.) and broadcasts a
`keelson.ReplayStatus` envelope on the `replay_status` subject at 1 Hz.

Unlike a one-shot replayer, the process stays alive after end-of-file —
either looping (if configured) or idling in `STOPPED` until a new
`play`/`load_file` arrives.

### Required CLI flags

| Flag | Purpose |
|---|---|
| `--realm` | Keelson base path (e.g. `trial`) |
| `--entity-id` | Replayer identity on the bus (e.g. `replayer`) |
| `--source-id` | Source ID for RPC keys and status publication |
| `--base-directory` | Root directory for `list_files` and relative `load_file` paths (default: cwd) |

### Optional initial-state flags

| Flag | Effect |
|---|---|
| `--mcap-file PATH` | Load this file at startup (absolute or relative to `--base-directory`) |
| `--loop` | Initial loop setting; toggleable later via `set_loop` |
| `--start-paused` | When `--mcap-file` is given, load but stay PAUSED instead of auto-playing |
| `--replay-key-tag` | Append `/replay` to every published topic |

### RPC interface

All procedures live under
`{realm}/@v0/{entity-id}/@rpc/{procedure}/{source-id}`. Request and response
types are defined in [interfaces/McapReplayControl.proto](../../interfaces/McapReplayControl.proto).

| Procedure | Request | Response |
|---|---|---|
| `get_status` | `Empty` | `ReplayStatus` (state, playhead, speed, loop, channel/message counts) |
| `list_files` | `ListFilesRequest{pattern}` | `ListFilesResponse{base_directory, files[]}` |
| `load_file` | `LoadFileRequest{path}` | `McapReplaySuccessResponse` |
| `play` | `Empty` | `McapReplaySuccessResponse` |
| `pause` | `Empty` | `McapReplaySuccessResponse` |
| `stop` | `Empty` | `McapReplaySuccessResponse` |
| `seek` | `SeekRequest{target}` | `McapReplaySuccessResponse` |
| `set_speed` | `SetSpeedRequest{speed}` (range [0.25, 4.0]) | `McapReplaySuccessResponse` |
| `set_loop` | `SetLoopRequest{loop}` | `McapReplaySuccessResponse` |

Errors are returned via Zenoh's `reply_err` channel with an
`ErrorResponse{error_description}` payload.

### Status broadcast

The connector also publishes a `keelson.ReplayStatus` envelope at 1 Hz on:

```
{realm}/@v0/{entity-id}/pubsub/replay_status/{source-id}
```

Subscribers can monitor playback without polling `get_status`.

### Run in container

```bash
docker run --rm ghcr.io/rise-maritime/keelson "mcap2keelson -h"

docker run --rm --network host \
  --volume /home/user/rec:/rec \
  ghcr.io/rise-maritime/keelson \
  "mcap2keelson --realm trial --entity-id replayer --source-id 0 \
                --base-directory /rec --mcap-file 2024-05-15.mcap"
```

### Debug or run within dev-container

```sh
# Start the daemon (loads file immediately and plays it)
uv run python connectors/mcap/bin/mcap2keelson.py \
  --realm trial --entity-id replayer --source-id 0 \
  --base-directory ./recordings --mcap-file ./recordings/2024.mcap

# Start headless (idle in STOPPED until load_file RPC arrives)
uv run python connectors/mcap/bin/mcap2keelson.py \
  --realm trial --entity-id replayer --source-id 0 \
  --base-directory ./recordings

# Drive it from another shell with zenoh-cli (or a Python session):
# subscribe to status
zenoh-cli -m peer sub -k 'trial/@v0/replayer/pubsub/replay_status/0'

# load → play → pause → seek (see ZENOH_API.md style examples elsewhere
# in the repo for full payload encoding)
```
