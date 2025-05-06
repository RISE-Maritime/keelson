# mediamtx

Connector towards [MediaMTX](https://github.com/bluenviron/mediamtx).

Each subcommand is explained in detail below.

## `whep`

This subcommands enables signalling (i.e. handshaking for WebRTC connections) over Zenoh. It achieves this by setting up a a zenoh proxy for the WHEP endpoint exposed by MediaMTX. WHEP is a standardized WebRTC Egress Protocol over HTTP. So, MediaMTX exposes a REST endpoint over HTTP that conforms to the WHEP standard. This subbcommand creates a zenoh queryable that maps to that REST endpoint. It looks something like this:

```
MediaMTX <- WHEP endpoint <- Zenoh queryable <--------  Zenoh infrastructure --------- Zenoh GET requester
```

The subcommand implements the [`WHEPProxy`](https://rise-maritime.github.io/keelson/interfaces/#whepproxy) interface

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
    image: ghcr.io/mo-rise/keelson
    restart: unless-stopped
    command: [
        "mediamtx -r <realm> -e <entity> whep -i mediamtx -m http://mediamtx:8889"
    ]

```
