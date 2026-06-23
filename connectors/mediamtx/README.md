# mediamtx

Connector towards [MediaMTX](https://github.com/bluenviron/mediamtx).

Each subcommand is explained in detail below.

## `whep`

This subcommands enables signalling (i.e. handshaking for WebRTC connections) over Zenoh. It achieves this by setting up a a zenoh proxy for the WHEP endpoint exposed by MediaMTX. WHEP is a standardized WebRTC Egress Protocol over HTTP. So, MediaMTX exposes a REST endpoint over HTTP that conforms to the WHEP standard. This subbcommand creates a zenoh queryable that maps to that REST endpoint. It looks something like this:

```
MediaMTX <- WHEP endpoint <- Zenoh queryable <--------  Zenoh infrastructure --------- Zenoh GET requester
```

The subcommand implements the [`WHEPProxy`](https://rise-maritime.github.io/keelson/interfaces/#whepproxy) interface

### Usage

```
usage: mediamtx [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                [--connect CONNECT] [--listen LISTEN] -r REALM -e ENTITY_ID
                {whep} ...

subcommands:
  whep                  WHEP signalling proxy

whep options:
  --whep-host WHEP_HOST
                        MediaMTX WHEP endpoint base URL (required)
  -i RESPONDER_ID, --responder-id RESPONDER_ID
                        Zenoh responder ID (required)
  -t TIMEOUT, --timeout TIMEOUT
                        HTTP request timeout in seconds (default: 8)
```

The setup at the MediaMTX end looks something like this:
```yaml
version: '3.9'

services:

  # A MediaMTX instance with a single configured source on path: /example
  mediamtx:
    image: bluenviron/mediamtx
    restart: unless-stopped
    environment:
      - MTX_PATHS_<pathname>_SOURCE=rtsp://url-to-your-camera
      # Important! Define a STUN server so that we can advertise our public IP as part of the ICE Candidates
      - MTX_WEBRTCICESERVERS2_0_URL=stun:stun.l.google.com:19302

  # Setting up a proxy for WHEP signalling over Zenoh for remote access to live streams.
  whep-proxy:
    image: ghcr.io/rise-maritime/keelson
    restart: unless-stopped
    command: [
        "mediamtx -r <realm> -e <entity> whep -i mediamtx --whep-host http://mediamtx:8889"
    ]

```

### Browser debug client

[`examples/browser-client-example.html`](examples/browser-client-example.html) is a
self-contained, dependency-free demo page for quickly viewing a stream and
debugging the signalling path. It does the WebRTC offer/answer handshake by
sending a Zenoh `GET` query (a `WHEPRequest`) to the connector's `whep_signal`
queryable and applying the returned `WHEPResponse` SDP — i.e. it exercises the
exact same `WHEPProxy` path a real client would use, end to end.

Because browsers can't speak the Zenoh protocol natively, the page talks to
Zenoh over the
[`zenoh-ts`](https://github.com/eclipse-zenoh/zenoh-ts) remote-api WebSocket
(loaded from a CDN, pinned to match the project's Zenoh `1.7.x`). That requires a
Zenoh router running the `zenoh-plugin-remote-api` plugin, listening on a
WebSocket port (default `10000`):

```json5
// router config.json5
{
  mode: "router",
  plugins: {
    remote_api: { websocket_port: "10000" },
  },
}
```

Open the file in a browser (e.g. `python -m http.server` from this directory),
fill in the remote-api WebSocket URL, realm, entity id, responder id and the
MediaMTX path, then click **Start**. The page constructs the RPC key as
`{realm}/@v0/{entity}/@rpc/whep_signal/{responder_id}`.

## NAT traversal (CGNAT) — TURN

WHEP signalling over Zenoh works through any NAT, but the **media** is a direct
WebRTC connection. When MediaMTX runs behind **CGNAT** (e.g. a vessel on a 4G
router), it has no externally reachable address and STUN can't help — the only
candidate a viewer can reach is a **TURN relay candidate**. So MediaMTX must be
configured with a TURN server, and `clientOnly` must stay unset/`false` so
MediaMTX itself allocates a relay (that's the bit that fixes CGNAT):

```
MTX_WEBRTCICESERVERS2_0_URL=stun:stun.l.google.com:19302
MTX_WEBRTCICESERVERS2_1_URL=turns:turn.<domain>:443?transport=tcp
MTX_WEBRTCICESERVERS2_1_USERNAME=turnuser
MTX_WEBRTCICESERVERS2_1_PASSWORD=<static-secret>
```

Notes:

- **Use TLS/TCP on `443`** (`turns:…?transport=tcp`) — carriers often filter UDP
  and odd ports. Don't add unreachable entries (e.g. `:80` UDP); MediaMTX's ICE
  gathering waits on them and can blow past the WHEP/Zenoh timeouts.
- **Self-host coturn** for static, non-metered credentials — example in
  [`examples/coturn/`](examples/coturn/).
- **No UDP at all?** Force relay-only on both sides (browser
  `iceTransportPolicy: "relay"` + `turns:…?transport=tcp`); all media is then
  TCP-relayed through coturn.

The browser debug client above has matching TURN URL / username / credential
fields and a **Force relay** checkbox for verifying the relay path.

### Ready-to-use examples

- [`examples/mediamtx-turn/docker-compose.yml`](examples/mediamtx-turn/docker-compose.yml)
  — MediaMTX + WHEP proxy wired to a TURN server, drop-in.
- [`examples/coturn/`](examples/coturn/) — self-hosted coturn behind Traefik
  (setup + troubleshooting in its [README](examples/coturn/README.md)).
- [`examples/verify-whep-turn.py`](examples/verify-whep-turn.py) — end-to-end
  deployment check (needs `pip install aiortc`); see `-h`.
