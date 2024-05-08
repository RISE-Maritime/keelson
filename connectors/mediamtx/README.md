# mediamtx

Interface towards [MediaMTX](https://github.com/bluenviron/mediamtx).

Each subcommand is explained in detail below.

## `whep`

This subcommands enables signalling (i.e. handshaking for WebRTC connections) over Zenoh. It achieves this by setting up a a zenoh proxy for the WHEP endpoint exposed by MediaMTX. WHEP is a standardized WebRTC Egress Protocol over HTTP. So, MediaMTX exposes a REST endpoint over HTTP that conforms to the WHEP standard. This subbcommand creates a zenoh queryable that maps to that REST endpoint. It looks something like this:

```
MediaMTX <- WHEP endpoint <- Zenoh queryable <--------  Zenoh infrastructure --------- Zenoh GET requester
```

The zenoh queryable requires JSON payloads of the following format:
```json
{
  "path": "the-pathname-of-the-stream-in-the-mediamtx-instance",
  "sdp": "the-sdp-from-the-requester"
}
```

And responds with a JSON payload of the following format:
```json
{
  "sdp": "the-sdp-of-the-responding-media-mtx-instance"
}
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

  # Setting up a proxy for WHEP signalling over Zenoh for remote access to live streams.
  whep-proxy:
    image: ghcr.io/mo-rise/keelson
    restart: unless-stopped
    command: [
        "mediamtx -r realm -e entity whep -i mediamtx -m http://mediamtx:8889"
    ]

```
