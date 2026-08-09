# network_manager connector

Measures link quality between keelson entities and publishes the result on the
canonical `network_status` subject (`keelson.NetworkStatus`).

Each instance plays **both** roles — it answers `ping_network` and it pings the
peers it is configured with. That is deliberate: a ping needs a responder at the
far end, so a responder-only deployment can never measure anything itself. Run
one per site and the links between them become measurable.

```
network_manager2keelson --realm rise --entity-id ted \
    --source-id network --peers masslab,sf18

  declares   rise/@v0/ted/@rpc/ping_network/network
  pings      rise/@v0/{peer}/@rpc/ping_network/**
  publishes  rise/@v0/ted/pubsub/network_status/network
```

## What it measures

The exchange yields four timestamps, and `interfaces/NetworkPingPong.proto`
carries exactly the ones the standard NTP-style calculation needs:

| | | from |
|---|---|---|
| `t1` | ping sent | `NetworkPong.ping.sent_at` (pinger's clock) |
| `t2` | ping received | `NetworkPong.ping_received_at` (responder's clock) |
| `t3` | pong sent | `NetworkPong.sent_at` (responder's clock) |
| `t4` | pong received | measured locally |

```
round_trip_time_ms = (t4 - t1) - (t3 - t2)
latency_ms         = round_trip_time_ms / 2
clock_skew_ms      = ((t2 - t1) + (t3 - t4)) / 2
```

Subtracting `(t3 - t2)` matters: a responder that takes 200 ms to answer is not
a 200 ms slower *link*, and reporting it as one sends someone hunting a network
fault that is not there. The responder stamps `t3` as late as it can so its own
processing lands inside that term.

`clock_skew_ms` assumes a roughly symmetric path, as NTP does. Treat it as an
indicator that two clocks disagree, not as a correction to apply.

## Options

| Flag | Meaning |
|---|---|
| `--peers` | Comma-separated entity ids to ping. Empty = answer pings only. |
| `--interval` | Seconds between rounds (default 10) |
| `--timeout` | Seconds to wait for replies (default 2) |
| `--payload-bytes` | Padding added to each ping, for measuring under load |

The responder id is wildcarded with `**` when pinging, so a peer may run under
any source id — including a multi-segment one like `ins/3/sbg`. A single `*`
would silently match none of those.

**A peer that does not answer publishes nothing**, rather than a zero. A zero
round trip would read as a perfect link.

## Not implemented: the bandwidth stress test

The connector this replaces
([keelson-processor-network-manager](https://github.com/RISE-Maritime/keelson-processor-network-manager))
had `--start-mb/--end-mb/--step-mb` and separate `ping_up`, `ping_down` and
`ping_up_down` procedures. Upload and echo are expressible here — put bytes in
`NetworkPing.payload` — but a **download-only** test is not: `NetworkPing` has
no field telling the responder how large a reply to send. Adding one is an
`interfaces/NetworkPingPong.proto` change, so it is left out rather than
half-implemented.

## Why this lives in the monorepo

The old processor stopped working for a reason worth remembering: it imported
`keelson.payloads.NetworkPing_pb2`, a payload keelson deleted, and it still
spoke the pre-`@v0` key format (`rise/v0/masslab/rpc/network/ping`). Nothing
caught either, because it lived in its own repository and nothing built it
against current keelson. In-tree, CI compiles it against the definitions it
depends on.

## Tests

```bash
uv run pytest -vv -m "not e2e" connectors/network_manager/   # timing maths
uv run pytest -vv -m e2e connectors/network_manager/         # real zenoh session
```

The e2e tests open an in-process peer session, so no router is needed.
