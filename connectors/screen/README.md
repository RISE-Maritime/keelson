# screen

Screen capture connector for Keelson. It captures a whole monitor or a single application window and publishes the frames as JPEG `image_compressed` (`foxglove.CompressedImage`) on the Zenoh bus.

The [camera](../camera) connector uses the same subject and payload. So the [MCAP](../mcap) recorder, replay and the [Foxglove](../foxglove) bridge treat a screen capture exactly like a camera feed, and nothing needs to be configured for it. Recording a session, replaying it, and scrubbing through it next to the camera images just works.

- **Monitor mode** (`--monitor N`, the default): `1..N` selects a single monitor and `0` captures all monitors combined. Run `--list-monitors` to see what is available.
- **Window mode** (`--window TEXT` or `--window-id ID`): follows a window as it moves or resizes. `TEXT` is matched case-insensitively against the app name and the window title, and the frontmost match is chosen. Once a window is picked, the connector stays on that window even if another match comes to the front. Run `--list-windows` to see what is available. The window's *on-screen rectangle* is what gets captured, so anything overlapping it is captured too. If the window is closed or minimised, the connector waits for it to come back.
- `--region X,Y,W,H` crops to a rectangle relative to the monitor or window.
- Frames are throttled to `--fps` (default 5), JPEG-encoded at `--jpeg-quality` (default 80), and downscaled to `--max-width` (default 1920, `0` keeps full resolution). Each payload carries the capture time in `CompressedImage.timestamp`.

## Platform notes

Capture uses [`mss`](https://github.com/BoboTiG/python-mss), and windows are looked up with each platform's native API. The connector has to run **natively on the machine whose screen it captures**: a container cannot see a host's screen, apart from Linux with the X11 socket mounted.

| Platform | Monitor capture | Window lookup | Notes |
|---|---|---|---|
| macOS | yes | Quartz (`pyobjc-framework-Quartz`) | The app running the process (Terminal, VS Code, …) needs **Screen Recording** permission in System Settings → Privacy & Security. Without it, captures show only the desktop wallpaper and window titles are empty, and the connector logs a warning at startup. Coordinates are in points, not Retina pixels. |
| Linux | X11 (and XWayland) | X11 / EWMH (`python-xlib`) | Native Wayland sessions are not supported. In Docker, mount `/tmp/.X11-unix` and set `DISPLAY`. |
| Windows | yes | `user32` (ctypes) | Window bounds exclude the invisible resize border. |

## Examples

```bash
# What can be captured?
screen2keelson --list-monitors
screen2keelson --list-windows

# Primary monitor at 5 fps
screen2keelson -r rise -e landkrabba -s screen/0 --monitor 1

# A single app window (e.g. the ECDIS), at 2 fps, full resolution
screen2keelson -r rise -e landkrabba -s screen/ecdis --window "ecdis" --fps 2 --max-width 0

# The top-left 1280x720 of monitor 2
screen2keelson -r rise -e landkrabba -s screen/1 --monitor 2 --region 0,0,1280,720

# Record it (together with everything else on the vessel) and replay later
keelson2mcap -k "rise/@v0/landkrabba/pubsub/**" --output-folder ./rec
mcap2keelson -r rise -e landkrabba -s replay/0 -mf ./rec/<file>.mcap
```

Frames are published on `{realm}/@v0/{entity_id}/pubsub/image_compressed/{source_id}`. Use a `source_id` under `screen/` to tell screen captures apart from cameras (`camera/...`).

## `screen2keelson`

```
usage: screen2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                      [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] [-r REALM] [-e ENTITY_ID]
                      [-s SOURCE_ID] [--monitor MONITOR | -w WINDOW | --window-id WINDOW_ID]
                      [--region X,Y,W,H] [--fps FPS] [-q [1-100]] [--max-width MAX_WIDTH]
                      [-f FRAME_ID] [--list-monitors] [--list-windows]

Capture a monitor or an application window and publish it as JPEG image_compressed to
Keelson/Zenoh

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
  -r, --realm REALM     Unique id for a domain/realm to connect (e.g. rise) [required] (default:
                        None)
  -e, --entity-id ENTITY_ID
                        Entity being a unique id representing an entity within the realm (e.g.
                        landkrabba) [required] (default: None)
  -s, --source-id SOURCE_ID
                        Source identifier (e.g. screen/0) [required] (default: None)
  --monitor MONITOR     Monitor to capture (0 = all monitors combined, 1.. = single monitor; see
                        --list-monitors) (default: 1)
  -w, --window WINDOW   Capture the frontmost window whose app name or title contains this text
                        (case-insensitive; see --list-windows) (default: None)
  --window-id WINDOW_ID
                        Capture the window with this id (from --list-windows) (default: None)
  --region X,Y,W,H      Crop to this rectangle, relative to the monitor or window (default: None)
  --fps FPS             Capture rate in frames per second (default: 5.0)
  -q, --jpeg-quality [1-100]
                        JPEG quality (default: 80)
  --max-width MAX_WIDTH
                        Downscale frames wider than this (keeping aspect ratio); 0 disables
                        (default: 1920)
  -f, --frame-id FRAME_ID
                        Frame ID to include in image payloads (default: None)
  --list-monitors       List monitors and exit (default: False)
  --list-windows        List on-screen windows and exit (default: False)
```
