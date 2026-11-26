# camera

Generic camera connector for Keelson. Captures video frames from any OpenCV-compatible source (RTSP, USB, file, etc.) and publishes them as compressed or raw images on the Zenoh bus.

## `camera2keelson`

```
usage: camera2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                      [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                      -s SOURCE_ID -u CAMERA_URL [--width WIDTH] [--height HEIGHT] [--fps FPS]
                      [--fourcc FOURCC] [--send {raw,webp,jpeg,png}] [--save {raw,webp,jpeg,png}]
                      [--save-path SAVE_PATH] [-f FRAME_ID] [--calibration-file CALIBRATION_FILE]
                      [--calibration-interval CALIBRATION_INTERVAL]

Capture video frames and publish to Keelson/Zenoh

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
  -r, --realm REALM     Unique id for a domain/realm to connect (e.g. rise)
  -e, --entity-id ENTITY_ID
                        Entity being a unique id representing an entity within the realm (e.g.
                        landkrabba)
  -s, --source-id SOURCE_ID
                        Source identifier (e.g. camera/0)
  -u, --camera-url CAMERA_URL
                        RTSP URL or any other video source that OpenCV can handle; a bare number
                        (0, 1, ...) is a local device index
  --width WIDTH         Requested capture width in pixels (with --height). A request: the log says
                        what the device granted; streams and files ignore it. (default: None)
  --height HEIGHT       Requested capture height in pixels (with --width) (default: None)
  --fps FPS             Requested capture frame rate (default: None)
  --fourcc FOURCC       Requested pixel format as a FourCC, e.g. MJPG or YUYV. Many USB cameras
                        reach their larger sizes and frame rates only in MJPG. (default: None)
  --send {raw,webp,jpeg,png}
                        Format to publish frames in (default: None)
  --save {raw,webp,jpeg,png}
                        Format to save frames to disk in (default: None)
  --save-path SAVE_PATH
                        Directory path to save frames to (default: ./rec)
  -f, --frame-id FRAME_ID
                        Frame ID to include in image payloads (default: None)
  --calibration-file CALIBRATION_FILE
                        Path to a JSON file with camera calibration parameters (width, height,
                        distortion_model, D, K, R, P). (default: None)
  --calibration-interval CALIBRATION_INTERVAL
                        Interval (seconds) at which calibration data is re-published. (default:
                        10)
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

# A USB camera at 1920x1080, 30 fps, in MJPG
uv run python connectors/camera/bin/camera2keelson.py \
  -r rise -e landkrabba -s camera/0 \
  -u /dev/video0 \
  --width 1920 --height 1080 --fps 30 --fourcc MJPG \
  --send jpeg

# Publish with camera calibration (re-published every 30s)
uv run python connectors/camera/bin/camera2keelson.py \
  -r rise -e landkrabba -s camera/bow \
  -u "rtsp://192.168.1.100:554/stream1" \
  --send jpeg \
  --calibration-file calibration.json \
  --calibration-interval 30
```

### Resolution, frame rate and pixel format

`--width`/`--height`, `--fps` and `--fourcc` are requests to the capture
device. Without them the device opens in its driver's default mode, often
640x480. The driver rounds a request to the nearest mode it supports, so the
connector logs the mode it actually got (`Capture mode: 1920x1080 @ 30 fps
MJPG`) and warns when that differs from what was asked. A stream or a file
ignores these settings. Many USB cameras reach their larger sizes and frame
rates only in `MJPG`; `v4l2-ctl --list-formats-ext` lists what a Linux device
offers.

### Calibration file format

The calibration file is a JSON file validated against `calibration-schema.json`. It follows the OpenCV/ROS pinhole camera model:

```json
{
  "width": 1920,
  "height": 1080,
  "distortion_model": "plumb_bob",
  "D": [0.0, 0.0, 0.0, 0.0, 0.0],
  "K": [500.0, 0.0, 960.0, 0.0, 500.0, 540.0, 0.0, 0.0, 1.0],
  "R": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
  "P": [500.0, 0.0, 960.0, 0.0, 0.0, 500.0, 540.0, 0.0, 0.0, 0.0, 1.0, 0.0]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `width` | Yes | Image width in pixels |
| `height` | Yes | Image height in pixels |
| `distortion_model` | No | Distortion model name (e.g. `"plumb_bob"`, `"rational_polynomial"`) |
| `D` | No | Distortion coefficients (variable length) |
| `K` | No | 3x3 intrinsic camera matrix (9 elements, row-major) |
| `R` | No | 3x3 rectification matrix (9 elements, row-major) |
| `P` | No | 3x4 projection matrix (12 elements, row-major) |
