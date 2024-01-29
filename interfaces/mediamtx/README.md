# keelson-interface-mediamtx

Interface towards video sources, primarily targeting: [MediaMTX](https://github.com/bluenviron/mediamtx).

Example usage:
```yaml
version: '3.9'

services:

  # A MediaMTX instance with a single configured source on path: /example
  mediamtx:
    image: bluenviron/mediamtx
    restart: unless-stopped
    network_mode: host
    environment:
      - MTX_PATHS_EXAMPLE_SOURCE=rtsp://url-to-your-camera

  # Grabbing each frame of the rtsp stream and push to Zenoh using keelson-brefv
  rtsp-to-frames:
    image: ghcr.io/mo-rise/keelson
    restart: unless-stopped
    command: [
        "rtsp to_frames -r realm -e entity -i demonstration -s example -u http://mediamtx:8554/example"
    ]

  # Setting up a proxy for WHEP signalling over Zenoh for remote access to live streams.
  whep-proxy:
    image: ghcr.io/mo-rise/keelson
    restart: unless-stopped
    command: [
        "whep-proxy -r realm -e entity -i demonstration -m http://mediamtx:8554"
    ]

```