# Keelson Connector - Joystick/Gamepad Controllers

Reads joystick and gamepad controllers and publishes axes and button events to the Keelson/Zenoh maritime data protocol. Runs in Docker on **Linux, macOS, and Windows**.

## Supported Controllers

| Controller | Flag | Subject Naming | Shift Logic |
|------------|------|---------------|-------------|
| [Seascape ROV Hand Controller](https://www.seascapesubsea.com/product/rov-hand-controller/) | `--controller ssrov` (default) | ROV-function names (`arm`, `joystick_x_pct`) | Yes (button 9) |
| Logitech F310 / F710 Gamepad | `--controller logitech` | Hardware names (`a`, `joystick_x_pct`) | No |

[SSROV DATASHEET.pdf](./doc/SSROV-HC-DATASHEET.pdf) | [SSROV MANUAL.pdf](./doc/SSROV-HC-MANUAL.pdf)

---

## Overview

**Key Features:**
- Real-time axes updates and fast button detection (press & release events)
- Same joystick HID interface used by QGroundControl
- Cross-platform via TCP relay — works identically on Linux, macOS, and Windows
- Docker container deployment (single compose file)

**Why Joystick Interface?**

The Seascape controller exposes two interfaces:
- `/dev/ttyACM1` - Serial interface (slower, custom text protocol)
- `/dev/input/js0` - **Joystick HID interface (RECOMMENDED)** - same as QGroundControl uses

---

## Quick Start

The same flow works on Linux, macOS, and Windows: a host-side relay reads the controller (pygame) and forwards events over TCP to the container.

> ⚠️ **The relay MUST run on the host, not in Docker, and MUST be started before `docker compose up`.**
> Docker Desktop on macOS and Windows runs containers inside a Linux VM that has no access to host USB devices,
> so pygame cannot see the controller from inside a container. The relay is the bridge — it reads the USB
> joystick natively on your OS and exposes it over TCP for the container to consume. There is no way to start
> the relay from a compose service; it has to be a host process. If the relay isn't running when the container
> starts, you'll see `Cannot connect to relay: [Errno 111] Connection refused` in the container logs.

```bash
# 1. Start relay(s) on the HOST — one per controller, separate ports, each in its own terminal

# MAC
uv run bin/hid_relay.py --port 9090                             # SSROV
uv run bin/hid_relay.py --no-mfi --port 9091 --joystick-index 0 # Logitech

# 2. THEN start the container(s) in another terminal
docker compose -f docker-compose.hc.yml --profile ssrov up                       # SSROV only
docker compose -f docker-compose.hc.yml --profile logitech up                    # Logitech only
docker compose -f docker-compose.hc.yml --profile ssrov --profile logitech up    # Both
```

**Running on bare-metal Linux without Docker?** You can read the device directly:
`python3 bin/hc2keelson -r rise -e rov --device /dev/input/js0`

> **macOS note:** The `--no-mfi` flag is required for the Logitech F310 on macOS. Apple's
> GCController framework exclusively claims known gamepads, hiding them from SDL. Running
> `--no-mfi` disables this and uses IOKit instead. The SSROV must run without `--no-mfi`
> (in a separate relay instance) because IOKit doesn't deliver events for it on macOS.

```
[SSROV]    --> [hid_relay.py :9090] --> [Docker: --controller ssrov]    --> Zenoh/Keelson
[Logitech] --> [hid_relay.py :9091] --> [Docker: --controller logitech] --> Zenoh/Keelson
```

---

## Published Keelson Subjects

Subject names follow the canonical [keelson 0.5.x](https://rise-maritime.github.io/keelson/) definitions in `messages/subjects.yaml`. Axis subjects carry the `_pct` suffix and values in **percent**.

| Subject | Type | Description | Value Range |
|---------|------|-------------|-------------|
| `joystick_x_pct` | TimestampedFloat | X axis (left-right) | -100.0 to 100.0 |
| `joystick_y_pct` | TimestampedFloat | Y axis (forward-back) | -100.0 to 100.0 |
| `joystick_z_pct` | TimestampedFloat | Z axis / throttle | -100.0 to 100.0 |
| `joystick_rx_pct` | TimestampedFloat | Rotation X (roll) | -100.0 to 100.0 |
| `joystick_ry_pct` | TimestampedFloat | Rotation Y (pitch) | -100.0 to 100.0 |
| `joystick_rz_pct` | TimestampedFloat | Rotation Z (twist/yaw) | -100.0 to 100.0 |
| `dpad_x_pct` | TimestampedFloat | D-pad horizontal | -100.0 / 0.0 / 100.0 |
| `dpad_y_pct` | TimestampedFloat | D-pad vertical | -100.0 / 0.0 / 100.0 |
| `joystick_lt_pct` | TimestampedFloat | Left trigger | 0.0 / 100.0 |
| `joystick_rt_pct` | TimestampedFloat | Right trigger | 0.0 / 100.0 |
| `button_state_change` | TimestampedInt | Button event | 1 (pressed) / 0 (released) |

**Key Expression Pattern:**

```
{realm}/@v0/{entity_id}/pubsub/{subject}/<controller_id>/<input_name>
# e.g.: rise/@v0/rov/pubsub/joystick_x_pct/ssrov/joystick_x_pct
# e.g.: rise/@v0/rov/pubsub/button_state_change/ssrov/arm
```

### SSROV Button Mapping (`--controller ssrov`)

| Button | Primary Function | Shift + Button |
|--------|-----------------|----------------|
| 1 | `grip_open` (Open Gripper) | `input_hold_set` |
| 2 | `grip_close` (Close Gripper) | `roll_pitch_toggle` |
| 3 (CCW) | `lights_up` | `trim_roll_inc` |
| 4 (CW) | `lights_down` | `trim_roll_dec` |
| 5 (CCW) | `gain_up` | `trim_pitch_inc` |
| 6 (CW) | `gain_down` | `trim_pitch_dec` |
| 7 | `tilt_up` | |
| 8 | `tilt_down` | |
| 9 | `shift` (modifier) | |
| 10 | `mode_manual` | |
| 11 | `mode_stabilize` | |
| 12 | `mode_depth_hold` | |
| 13 | `mode_poshold` | |
| 14 | `arm` | |
| 15 | `disarm` | |
| 16-19 | User-defined (`a`, `b`, `top_left`, `top_right`) | |

### Logitech F310/F710 Mapping (`--controller logitech`)

Generic naming — no shift logic. Set the hardware switch on back to **D** (DirectInput mode). Shows as "Logitech Dual Action".

**Axes:**

| Control | Subject | Value Range |
|---------|---------|-------------|
| Left Stick X | `joystick_x_pct` | -100.0 to 100.0 |
| Left Stick Y | `joystick_y_pct` | -100.0 to 100.0 |
| Right Stick X | `joystick_rx_pct` | -100.0 to 100.0 |
| Right Stick Y | `joystick_ry_pct` | -100.0 to 100.0 |
| D-pad X | `dpad_x_pct` | -100.0 / 0.0 / 100.0 |
| D-pad Y | `dpad_y_pct` | -100.0 / 0.0 / 100.0 |
| LT (digital trigger) | `joystick_lt_pct` | 0.0 / 100.0 |
| RT (digital trigger) | `joystick_rt_pct` | 0.0 / 100.0 |

**Buttons:** All buttons publish to `button_state_change` (1=pressed, 0=released). Source-id carries the button name (e.g. `logitech/a`); unmapped buttons use the numeric index.

| Button # | Physical Control |
|----------|-----------------|
| 0 | X (left) |
| 1 | A (bottom) |
| 2 | B (right) |
| 3 | Y (top) |
| 4 | LB |
| 5 | RB |
| 6 | LT |
| 7 | RT |
| 8 | Back |
| 9 | Start |
| 10 | L3 (left stick click) |
| 11 | R3 (right stick click) |
| 12 | D-pad Up |
| 13 | D-pad Down |
| 14 | D-pad Left |
| 15 | D-pad Right |

### Custom Controller Profiles

Profiles are YAML files in [profiles/](profiles/) — `ssrov.yaml` and `logitech.yaml` ship with the project. To support a new controller, drop a YAML file in `profiles/` (use `--controller name` to pick it up) or anywhere else (use `--controller-config /path/to/file.yaml`).

The schema mirrors the bundled examples — see [profiles/ssrov.yaml](profiles/ssrov.yaml). Required keys: `axis_map` and `button_name_map` (both `int -> str`). Optional: `button_to_axis` (digital triggers published as axis values), `shift_button` (button index acting as a modifier), `shift_map` (shifted-name overrides for buttons while shift is held).

In the container, custom profiles can be mounted at `/usr/local/share/hc-profiles/<name>.yaml` and selected with `--controller <name>`, or mounted anywhere and selected with `--controller-config <path>`. The `HC_PROFILES_DIR` env var overrides the search path.

---

## Usage

### Command-Line Options

```
Arguments:
  -r, --realm REALM              Keelson realm (default: "rise")
  -e, --entity-id ENTITY_ID      Entity identifier (default: "rov")

Device Configuration:
  --device, -d DEVICE            Joystick device path (default: "/dev/input/js0")
  --relay HOST:PORT              TCP relay address for cross-platform mode
                                 (e.g. --relay host.docker.internal:9090)
  --relay-max-retries N          Max relay connect attempts before exit
                                 (default: 0 = unlimited; backoff 1s..30s)
  -c, --controller NAME          Built-in profile name (resolves to
                                 profiles/<name>.yaml). Default: ssrov
  --controller-config PATH       Path to a custom controller-profile YAML.
                                 Overrides --controller.

Rate limiting (per-axis):
  --axis-min-interval-ms MS      Skip publish if last publish was <MS ago
                                 AND value barely changed (default: 30)
  --axis-min-change PCT          Percentage-point change that always
                                 forces an immediate publish (default: 1.0)
  --axis-center-snap-pct PCT     Snap |value| < PCT to exactly 0.0 before
                                 rate-limiting. Cleans up joystick ADC rest
                                 offset so a released stick publishes 0.0
                                 instead of a residual like -0.39.
                                 0 disables; recommended 2.0 (default: 0.0)

Zenoh Configuration:
  --mode {peer,client}           Zenoh session mode (default: peer)
  --connect ENDPOINT             Zenoh router endpoint (can be repeated)

Logging:
  --log-level LEVEL              10=DEBUG, 20=INFO, 30=WARN (default: 20)
  --log-json                     One JSON object per line (for container
                                 log pipelines like Loki/Datadog/GCP)
```

> ℹ️ **Axis and D-pad events log at DEBUG (10); button events at INFO (20).** Run with
> `--log-level 10` to verify joystick / D-pad activity is actually being received — at the
> default INFO level you'll only see button press/release lines, even though axes and D-pad
> are being published normally.

Source-id is constructed automatically as `<controller_id>/<input_name>` from the `--controller` flag (e.g. `ssrov/joystick_x_pct`, `logitech/a`).

### Examples

```bash
# Basic usage (Linux, auto-detects /dev/input/js0)
python3 bin/hc2keelson -r rise -e rov

# Custom joystick device
python3 bin/hc2keelson -r rise -e rov --device /dev/input/js1

# TCP relay mode (macOS/Windows)
python3 bin/hc2keelson -r rise -e rov --relay host.docker.internal:9090

# Logitech gamepad via relay
python3 bin/hc2keelson -r rise -e rov --controller logitech --relay host.docker.internal:9091

# Connect to specific Zenoh router
python3 bin/hc2keelson -r rise -e rov \
  --mode client --connect tcp/192.168.1.100:7447
```

---

## Docker Deployment

### Docker Compose Profiles

| Profile    | Controller         | Platform | Description                                       |
|------------|--------------------|----------|---------------------------------------------------|
| `ssrov`    | SSROV              | All      | Connects to host-side `hid_relay.py` on port 9090 |
| `logitech` | Logitech F310/F710 | All      | Connects to host-side `hid_relay.py` on port 9091 |

To customise (realm, entity-id, log level, port, Zenoh router), edit the `command:` line of the relevant service in `docker-compose.hc.yml`. For a non-default Zenoh router, append `--mode client --connect tcp/host:7447` to the command.

> ⚠️ **The host-side `hid_relay.py` is not — and cannot be — part of docker compose.** It must be running on
> the host before `docker compose up`, because Docker Desktop on macOS/Windows can't see USB devices from
> inside containers. Start one relay per controller (each on its own port) in separate terminals first.

```bash
# Start relay on host first (separate terminal, NOT inside Docker)
uv run bin/hid_relay.py --port 9090

# Then start container (SSROV)
docker compose -f docker-compose.hc.yml --profile ssrov up

# Logitech
docker compose -f docker-compose.hc.yml --profile logitech up

# Both controllers
docker compose -f docker-compose.hc.yml --profile ssrov --profile logitech up
```

### Build Image

```bash
docker build -t keelson-connector-hand-controller .
```

---

## Host Relay (hid_relay.py)

The relay script reads the joystick on the host using pygame and forwards events over TCP to the container. It uses the same 8-byte binary format as the Linux joystick API.

```bash
# List available joysticks
uv run bin/hid_relay.py --list

# Start relay (default port 9090)
uv run bin/hid_relay.py

# Use second joystick on custom port
uv run bin/hid_relay.py --joystick-index 1 --port 5000

# Remap axis indices (pygame index -> wire index)
uv run bin/hid_relay.py --axis-map '{"3":5}'

# Remap button indices
uv run bin/hid_relay.py --button-map '{"0":2}'
```

---

## Installation

### Prerequisites

- Python 3.13+
- Docker (recommended)
- USB connection to Seascape ROV Hand Controller
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install Dependencies

Dependencies are declared in [pyproject.toml](pyproject.toml) with optional groups.

```bash
# Container/runtime dependencies (installed automatically by Docker)
uv pip install --system .

# Host relay dependencies (only needed for macOS/Windows relay mode)
uv pip install --system ".[relay]"

# Development dependencies (black, pylint, pytest)
uv pip install --system ".[dev]"
```

---

## Development

### Project Structure

```
keelson-connector-hand-controller/
├── bin/
│   ├── hc2keelson            # Main connector script
│   ├── hid_relay.py          # Cross-platform host-side relay
│   ├── joystick_proto.py     # Shared HID protocol constants + parsing
│   └── terminal_inputs.py    # Argument parsing
├── profiles/
│   ├── ssrov.yaml            # Seascape ROV Hand Controller profile
│   └── logitech.yaml         # Logitech F310/F710 profile
├── tests/                    # pytest suite
├── examples/
│   └── joystick_reader.py    # Standalone joystick reader
├── doc/                      # Datasheets and documentation
├── docker-compose.hc.yml
├── Dockerfile
├── pyproject.toml            # Dependencies (runtime, [dev], [relay] extras)
└── README.md
```

### Linting

```bash
black --check bin/*    # Check formatting
black bin/*            # Auto-format
pylint bin/*           # Lint
```

### Testing Without Zenoh

```bash
# List available joystick devices (Linux)
python3 examples/joystick_reader.py --list

# Read controller data directly
python3 examples/joystick_reader.py --device /dev/input/js0

# List joysticks cross-platform (via pygame)
uv run bin/hid_relay.py --list
```

---

## Controller Data Format

The connector uses the standard Linux joystick event structure (8 bytes):

```c
struct js_event {
    uint32_t timestamp;  // Milliseconds since device opened
    int16_t  value;      // -32768 to 32767 for axes, 0/1 for buttons
    uint8_t  type;       // JS_EVENT_BUTTON (0x01) or JS_EVENT_AXIS (0x02)
    uint8_t  number;     // Button/axis number
};
```

The TCP relay uses this same binary format over the wire.

---

## Troubleshooting

### Controller Not Detected

```bash
# Linux: check joystick devices
ls -l /dev/input/js*
cat /sys/class/input/js0/device/name
# Should show: seascapesubsea ROV-Controller V1.0.2

# Any OS: list via pygame
uv run bin/hid_relay.py --list
```

### Permission Denied (Linux)

```bash
# Temporary
sudo chmod a+r /dev/input/js0

# Permanent - add user to input group
sudo usermod -a -G input $USER
# Log out and back in
```

### No Events Received

1. Verify controller is powered (LEDs should be lit)
2. Test with `python3 examples/joystick_reader.py` (Linux) or `uv run bin/hid_relay.py --list` (any OS)
3. Move joysticks and press buttons
4. Check kernel module: `lsmod | grep joydev` (Linux)
5. Enable debug logging — see below

### Joystick / D-pad Look Dead in Docker Logs (but buttons work)

**Symptom:** `docker compose -f docker-compose.hc.yml up` shows button press/release lines when
you press buttons, but moving the sticks or D-pad shows nothing — so it looks like axes and
D-pad aren't being recognized.

**Cause:** Logging is intentionally asymmetric to keep the log readable:

| Event | Log level | Visible at default INFO? |
| ----- | --------- | ------------------------ |
| Button press/release | `INFO` | yes |
| Joystick axis motion | `DEBUG` | no |
| D-pad (encoded as two axis events by the relay) | `DEBUG` | no |

Axes and D-pad still publish to Keelson normally — they just don't log at the default
`--log-level 20` (INFO).

**Verify they're being received** by switching the container to DEBUG. Edit the relevant
service's `command:` line in [docker-compose.hc.yml](docker-compose.hc.yml) and change
`--log-level 20` to `--log-level 10`:

```yaml
command: ["hc2keelson --log-level 10 -r rise -e ssrs18 --controller ssrov --relay host.docker.internal:9090"]
```

Restart the stack and you should now see lines like:

```text
Axis 0 (joystick_x_pct): 12345 -> 37.681
Ignoring unmapped axis 6: 32767
```

For D-pad confirmation on the **host-side relay** (separate process, separate log), look for:

```text
Hat (1,0) -> dpad_x=32767, dpad_y=0
```

> ⚠️ DEBUG produces a lot of output while sticks are moving — switch back to `--log-level 20`
> for normal operation.

### Relay Connection Issues (macOS/Windows)

**Symptom:** container logs show `Cannot connect to relay: [Errno 111] Connection refused` on a retry loop.

**Cause:** nothing is listening on the relay port on the host. The relay runs on the **host**, not in Docker —
it cannot be started by `docker compose up`. Containers on macOS/Windows have no access to host USB devices,
which is exactly why the relay exists as a separate host-side process.

**Fix:**

1. Start `hid_relay.py` on the host (one terminal per controller, separate ports) **before** `docker compose up`
2. Verify it's listening: `lsof -nP -iTCP:9090 -sTCP:LISTEN` (should show a python process)
3. Check that the relay port is not blocked by a firewall
4. The container uses `host.docker.internal` to reach the host — this works automatically on Docker Desktop

---

## References

- **Linux Joystick API**: [Documentation](https://www.kernel.org/doc/Documentation/input/joystick-api.txt)
- **Keelson Protocol**: [https://rise-maritime.github.io/keelson/](https://rise-maritime.github.io/keelson/)
- **Zenoh**: [https://zenoh.io/](https://zenoh.io/)
- **Skarv**: [https://freol35241.github.io/skarv/](https://freol35241.github.io/skarv/)
- **uv**: [https://docs.astral.sh/uv/](https://docs.astral.sh/uv/)

---

## Contributing

Contributions are welcome! This connector follows the design patterns established in:
- [keelson-connector-nmea](https://github.com/RISE-Maritime/keelson-connector-nmea)
- [keelson-connector-ais](https://github.com/RISE-Maritime/keelson-connector-ais)
