# mockups

A multitude of binaries providing mocked data for different payload types.

## `mockup-radar2keelson`

Generates fake radar spokes and sweeps for testing purposes.

```
usage: fake_radar [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                  [--connect CONNECT] [--listen LISTEN] -r REALM -e ENTITY_ID
                  -s SOURCE_ID [--spokes_per_sweep SPOKES_PER_SWEEP]
                  [--seconds_per_sweep SECONDS_PER_SWEEP]
                  [--spoke_resolution SPOKE_RESOLUTION]
                  [--spoke_range SPOKE_RANGE]

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  --spokes_per_sweep SPOKES_PER_SWEEP
                        (default: 2048)
  --seconds_per_sweep SECONDS_PER_SWEEP
                        (default: 2)
  --spoke_resolution SPOKE_RESOLUTION
                        (default: 512)
  --spoke_range SPOKE_RANGE
                        (default: 5000)
```

### Example

```bash
uv run python connectors/mockups/bin/mockup-radar2keelson.py \
  -r rise -e test_vessel -s radar/0 \
  --spokes_per_sweep 2048 --seconds_per_sweep 2
```
