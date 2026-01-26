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

Replays all messages from a recorded mcap file with **external control support** via Zenoh RPC.

### Features

- **Remote Control**: Start, stop, pause, seek, and adjust playback speed via Zenoh queryables
- **File Browsing**: List available MCAP files in a configured directory  
- **Status Broadcasting**: Periodic status updates published for subscribers to monitor progress
- **Speed Control**: Playback speeds from 0.25x to 4x
- **Looping**: Enable/disable loop mode via RPC

### Usage

```sh
usage: mcap-replay [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                   -r REALM -e ENTITY_ID [-i RESPONDER_ID] [-mf MCAP_FILE] [-md MCAP_DIRECTORY]
                   [--auto-play] [--loop] [--replay-key-tag] [--status-rate STATUS_RATE]

MCAP replay connector for keelson with external control support

Required arguments:
  -r REALM, --realm REALM
                        Keelson realm (base path for key expressions)
  -e ENTITY_ID, --entity-id ENTITY_ID
                        Entity ID for this replay instance

File options (at least one required):
  -mf MCAP_FILE, --mcap-file MCAP_FILE
                        MCAP file to load on startup
  -md MCAP_DIRECTORY, --mcap-directory MCAP_DIRECTORY
                        Directory to scan for MCAP files (enables list_files RPC)

Optional arguments:
  -i RESPONDER_ID, --responder-id RESPONDER_ID
                        Responder ID for RPC endpoints (default: default)
  --auto-play           Start playback immediately after loading file
  --loop                Enable looping by default
  --replay-key-tag      Append /replay to all published topic keys
  --status-rate STATUS_RATE
                        Rate at which to publish status updates in Hz (default: 1.0)
```

### RPC Endpoints (Queryables)

The service exposes the following RPC endpoints at `{realm}/@v0/{entity_id}/@rpc/{procedure}/{responder_id}`:

| Procedure | Request | Response | Description |
|-----------|---------|----------|-------------|
| `get_status` | Empty | `ReplayStatus` | Get current playback status and metadata |
| `list_files` | `ListFilesRequest` | `ListFilesResponse` | List available MCAP files (requires `--mcap-directory`) |
| `load_file` | `LoadFileRequest` | `McapReplaySuccessResponse` | Load an MCAP file for playback |
| `play` | Empty | `McapReplaySuccessResponse` | Start or resume playback |
| `pause` | Empty | `McapReplaySuccessResponse` | Pause playback |
| `stop` | Empty | `McapReplaySuccessResponse` | Stop playback and reset to beginning |
| `seek` | `SeekRequest` | `McapReplaySuccessResponse` | Seek to a specific timestamp |
| `set_speed` | `SetSpeedRequest` | `McapReplaySuccessResponse` | Change playback speed (0.25-4.0) |
| `set_loop` | `SetLoopRequest` | `McapReplaySuccessResponse` | Enable/disable looping |

### Status Publishing

Status updates are published periodically on: `{realm}/@v0/{entity_id}/pubsub/replay_status/{responder_id}`

The `ReplayStatus` payload includes:
- Current state (STOPPED, PLAYING, PAUSED, LOADING)
- Current playback timestamp
- Start/end timestamps of the loaded file
- Playback speed
- Progress percentage
- Messages played / total messages
- Loop enabled flag

### Example: Control from Python

```python
import zenoh
from keelson.interfaces.McapReplayControl_pb2 import (
    ReplayStatus, ListFilesResponse, SetSpeedRequest
)

session = zenoh.open(zenoh.Config())

# Get status
for reply in session.get("my-realm/@v0/mcap-replay/@rpc/get_status/default"):
    if reply.ok:
        status = ReplayStatus.FromString(reply.ok.payload.to_bytes())
        print(f"State: {status.state}, Progress: {status.progress_percent:.1f}%")

# List files
for reply in session.get("my-realm/@v0/mcap-replay/@rpc/list_files/default"):
    if reply.ok:
        resp = ListFilesResponse.FromString(reply.ok.payload.to_bytes())
        for f in resp.files:
            print(f"{f.path}: {f.message_count} messages")

# Play
session.get("my-realm/@v0/mcap-replay/@rpc/play/default")

# Set speed to 2x
req = SetSpeedRequest(speed=2.0)
session.get("my-realm/@v0/mcap-replay/@rpc/set_speed/default", payload=req.SerializeToString())
```

### Example: Control from TypeScript (zenoh-ts)

```typescript
import { Session } from "@aspect-dev/zenoh-ts";
import { ReplayStatus, ListFilesResponse, SetSpeedRequest } from "keelson/interfaces/McapReplayControl";

const session = await Session.open();

// Get status
const replies = await session.get("my-realm/@v0/mcap-replay/@rpc/get_status/default");
for (const reply of replies) {
  if (reply.payload) {
    const status = ReplayStatus.decode(reply.payload);
    console.log(`State: ${status.state}, Progress: ${status.progressPercent}%`);
  }
}

// Play
await session.get("my-realm/@v0/mcap-replay/@rpc/play/default");

// Set speed
const req = SetSpeedRequest.encode({ speed: 2.0 }).finish();
await session.get("my-realm/@v0/mcap-replay/@rpc/set_speed/default", { payload: req });
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
# Start with external control (directory mode for file browsing)
python3 connectors/mcap/bin/mcap-replay --log-level 20 -r my-realm -e mcap-replay -md ./recordings/

# Start with a specific file and auto-play
python3 connectors/mcap/bin/mcap-replay --log-level 20 -r my-realm -e mcap-replay -mf ./recording.mcap --auto-play

# Start with directory + initial file + looping
python3 connectors/mcap/bin/mcap-replay --log-level 20 -r my-realm -e mcap-replay -md ./recordings/ -mf ./recordings/test.mcap --loop

# With replay key tag (appends /replay to all topics)
python3 connectors/mcap/bin/mcap-replay --log-level 20 -r my-realm -e mcap-replay -md ./recordings/ --replay-key-tag
```

