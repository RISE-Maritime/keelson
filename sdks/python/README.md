# Keelson-SDK (python)

A python SDK for [keelson](https://github.com/RISE-Maritime/keelson).

## Basic usage

See the [tests](https://github.com/RISE-Maritime/keelson/blob/main/sdks/python/tests/test_sdk.py)

## Keelson codec for `zenoh-cli`

The python sdk also bundles a keelson codec for [`zenoh-cli`](https://github.com/MO-RISE/zenoh-cli). Installed next to it, `zenoh put` and `zenoh subscribe` can write and read keelson envelopes, so shell pipelines (e.g. [porla](https://github.com/RISE-Maritime/porla)) can exchange data with keelson directly. The codec picks the payload type from the subject in the key.

| Encoder / decoder | Subjects | Value |
|---|---|---|
| `keelson-enclose-from-text` / `keelson-uncover-to-text` | payload `TimestampedString` or `TimestampedBytes` (`raw`, `raw_nmea0183`, `raw_lwe450`, `raw_json`, `name`, ...) | one line of text; a trailing line terminator is dropped on enclose |
| `keelson-enclose-from-base64` / `keelson-uncover-to-base64` | payload `TimestampedBytes` (`raw`, `raw_nmea2000`, `raw_rtcm_v3`, ...) | base64, lossless for binary |
| `keelson-enclose-from-json` / `keelson-uncover-to-json` | any well-known subject | the payload as protobuf JSON |

The text and base64 codecs stamp the payload with the current time on enclose and drop the timestamp on uncover.

```bash
# NMEA 0183 lines from a UDP feed onto the bus
socat UDP4-RECV:10110,reuseaddr STDOUT \
  | zenoh put -k rise/@v0/vessel/pubsub/raw_nmea0183/porla/0 --line '{value}' --encoder keelson-enclose-from-text

# Positions off the bus as JSON
zenoh subscribe -k 'rise/@v0/vessel/pubsub/location_fix/**' --decoder keelson-uncover-to-json
```

Data put this way is not a full keelson producer: `zenoh put` declares no liveliness tokens and does not apply the subject's QoS profile from `qos.yaml`.
