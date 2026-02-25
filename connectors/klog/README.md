# klog

klog is very simple data logging to file interface for keelson. It provides two binaries:

## klog-record

  Records all envelopes on the user-defined subscription topics to a length-delimited binary file (a klog-file). Inspired by https://github.com/sebnyberg/ldproto-py

### Usage

```
usage: klog-record [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                   [--connect CONNECT] [--listen LISTEN] -k KEY -o OUTPUT

A pure python klog recorder for keelson

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -k KEY, --key KEY     Key expressions to subscribe to from the Zenoh session (default: None)
  -o OUTPUT, --output OUTPUT
                        File path to write recording to (default: None)
```

### Example run command

```bash
# Show help
docker run --rm ghcr.io/rise-maritime/keelson "klog-record -h"

# Record
docker run --rm --network host \
  --volume /home/user/rec_klog:/rec_klog \
  ghcr.io/rise-maritime/keelson \
  "klog-record --output /rec_klog/2024-05-15.klog -k rise/v0/my_vessel/pubsub/**"
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
                        (default: 20)
  -i INPUT, --input INPUT
                        File path to read klog file from (default: None)
  -o OUTPUT, --output OUTPUT
                        File path to write mcap file to (default: None)
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
