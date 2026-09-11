# klog

klog is very simple data logging to file interface for keelson. It provides two binaries:

## keelson2klog

  Records all envelopes on the user-defined subscription topics to a length-delimited binary file (a klog-file). Inspired by https://github.com/sebnyberg/ldproto-py

### Usage

```
usage: keelson2klog [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                    [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -k KEY -o OUTPUT

A pure python klog recorder for keelson

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
  -k, --key KEY         Key expressions to subscribe to from the Zenoh session (default: None)
  -o, --output OUTPUT   File path to write recording to (default: None)
```

### Example run command

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "keelson2klog -h"

# Record.
#
# Two -k patterns are required to capture both own-entity messages and
# observations of external entities published under the @target/ extension
# (e.g. AIS-tracked vessels). A single pubsub/** pattern silently misses
# every @target-extended key. See protocol spec §2.1.1.
docker run --rm --network host \
  --volume /home/user/rec_klog:/rec_klog \
  ghcr.io/rise-maritime/keelson \
  "keelson2klog --output /rec_klog/2024-05-15.klog \
               -k rise/v0/my_vessel/pubsub/** \
               -k rise/v0/my_vessel/pubsub/**/@target/**"
```


## klog2mcap

Converts a klog-file to a mcap-compatible file.

### Usage

```
usage: klog2mcap [-h] [--log-level LOG_LEVEL] -i INPUT -o OUTPUT

Converts from klog to mcap format.

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
  -i, --input INPUT     File path to read klog file from (default: None)
  -o, --output OUTPUT   File path to write mcap file to (default: None)
```

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "klog2mcap -h"

# Convert
docker run --rm --network host \
  --volume /home/user/rec_klog:/rec_klog \
  ghcr.io/rise-maritime/keelson \
  "klog2mcap --input /rec_klog/2024-05-15.klog --output /rec_klog/2024-05-15.mcap"
```
