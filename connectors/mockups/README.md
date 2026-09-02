# mockups

A multitude of binaries providing mocked data for different payload types.

## `mockup-radar2keelson`

Generates fake radar spokes and sweeps for testing purposes.

```
usage: mockup-radar2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                            [--connect CONNECT] [--listen LISTEN] [--zenoh-config ZENOH_CONFIG]
                            -r REALM -e ENTITY_ID -s SOURCE_ID
                            [--spokes_per_sweep SPOKES_PER_SWEEP]
                            [--seconds_per_sweep SECONDS_PER_SWEEP]
                            [--spoke_resolution SPOKE_RESOLUTION] [--spoke_range SPOKE_RANGE]

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
  --spokes_per_sweep SPOKES_PER_SWEEP
  --seconds_per_sweep SECONDS_PER_SWEEP
  --spoke_resolution SPOKE_RESOLUTION
  --spoke_range SPOKE_RANGE
```

### Example

```bash
uv run python connectors/mockups/bin/mockup-radar2keelson.py \
  -r rise -e test_vessel -s radar/0 \
  --spokes_per_sweep 2048 --seconds_per_sweep 2
```
