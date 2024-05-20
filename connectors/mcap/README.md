# mcap

Provides an interface to the [mcap] file format through two binaries:

## mcap-record

  Record envelopes to an mcap file injecting the appropriate message schemas for all well-known payloads.

```bash
# Show help 
docker run ghcr.io/mo-rise/keelson:0.3.4 "mcap-record -h"

# Record
# Crete an reccording folder before running, for this example "mkdir rec_mcap" 
docker run --network host --volume /home/user/rec_mcap:/rec_mcap ghcr.io/mo-rise/keelson:0.3.4 "mcap-record --output rec_mcap/2024-05-15.mcap -k rise/v0/masslab/pubsub/**"
```

## mcap-replay

  Replays all envelopes from a recorded mcap file.

```bash
# Show help 
docker run ghcr.io/mo-rise/keelson:0.3.4 "mcap-replay -h"

# Record
docker run --network host --volume /home/user/rec_mcap:/rec_mcap ghcr.io/mo-rise/keelson:0.3.4 "mcap-replay --input rec_mcap/2024-05-15.mcap"
```