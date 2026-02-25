# camera

Generic camera connector for Keelson. Captures video frames from any OpenCV-compatible source (RTSP, USB, file, etc.) and publishes them as compressed or raw images on the Zenoh bus.

## `camera2keelson`

```
usage: camera2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                      [--connect CONNECT] [--listen LISTEN] -r REALM -e
                      ENTITY_ID -s SOURCE_ID -u CAMERA_URL
                      [--send {raw,webp,jpeg,png}] [--save {raw,webp,jpeg,png}]
                      [--save-path SAVE_PATH] [-f FRAME_ID]

Capture video frames and publish to Keelson/Zenoh

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode {peer,client}, -m {peer,client}
                        The zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  -r REALM, --realm REALM
                        Unique id for a domain/realm to connect (e.g. rise) (default: None)
  -e ENTITY_ID, --entity-id ENTITY_ID
                        Entity being a unique id representing an entity within the realm
                        (e.g. landkrabba) (default: None)
  -s SOURCE_ID, --source-id SOURCE_ID
                        Source identifier (e.g. camera/0) (default: None)
  -u CAMERA_URL, --camera-url CAMERA_URL
                        RTSP URL or any other video source that OpenCV can handle (default: None)
  --send {raw,webp,jpeg,png}
                        Format to publish frames in (default: None)
  --save {raw,webp,jpeg,png}
                        Format to save frames to disk in (default: None)
  --save-path SAVE_PATH
                        Directory path to save frames to (default: ./rec)
  -f FRAME_ID, --frame-id FRAME_ID
                        Frame ID to include in image payloads (default: None)
```

### Example

```bash
# Publish compressed JPEG frames from an RTSP camera
uv run python connectors/camera/bin/camera2keelson.py \
  -r rise -e landkrabba -s camera/bow \
  -u "rtsp://192.168.1.100:554/stream1" \
  --send jpeg

# Capture from a USB camera (device 0)
uv run python connectors/camera/bin/camera2keelson.py \
  -r rise -e landkrabba -s camera/0 \
  -u 0 \
  --send webp
```
