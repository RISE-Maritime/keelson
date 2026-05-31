# Keelson Connector — Hand Controller

Reads joystick / gamepad controllers and publishes axes and button events to
the Keelson/Zenoh bus.

| Controller | Flag | Subject Naming | Shift Logic |
|------------|------|----------------|-------------|
| [Seascape ROV Hand Controller](https://www.seascapesubsea.com/product/rov-hand-controller/) | `--controller ssrov` (default) | ROV-function names (`arm`, `joystick_x_pct`) | Yes (button 9) |
| Logitech F310 / F710 Gamepad | `--controller logitech` | Hardware names (`a`, `joystick_x_pct`) | No |

Datasheets: [SSROV-HC-DATASHEET.pdf](./doc/SSROV-HC-DATASHEET.pdf), [SSROV-HC-MANUAL.pdf](./doc/SSROV-HC-MANUAL.pdf).

## Overview

The connector reads the standard Linux joystick HID interface
(`/dev/input/js0`) or, for Docker Desktop on macOS / Windows, a TCP relay
running on the host. The wire format is identical in both cases.

```
[Controller] → [pygame on HOST] → TCP:9090 → [Docker container] → Zenoh/Keelson
```

Docker Desktop on macOS / Windows runs containers inside a Linux VM with no
access to host USB, so pygame can't see the controller from inside the
container. The relay (`scripts/hid_relay.py`) is the bridge: it reads the
USB joystick natively on the host OS and exposes it over TCP for the
container to consume.

## Published Subjects

Subjects already exist in [`messages/subjects.yaml`](../../messages/subjects.yaml). All values use Keelson's standard payload types — no connector-specific payloads.

| Subject | Type | Description | Value Range |
|---------|------|-------------|-------------|
| `joystick_x_pct` | `TimestampedFloat` | X axis (left–right) | -100.0 .. 100.0 |
| `joystick_y_pct` | `TimestampedFloat` | Y axis (forward–back) | -100.0 .. 100.0 |
| `joystick_z_pct` | `TimestampedFloat` | Z axis / throttle | -100.0 .. 100.0 |
| `joystick_rx_pct` | `TimestampedFloat` | Rotation X (roll) | -100.0 .. 100.0 |
| `joystick_ry_pct` | `TimestampedFloat` | Rotation Y (pitch) | -100.0 .. 100.0 |
| `joystick_rz_pct` | `TimestampedFloat` | Rotation Z (twist/yaw) | -100.0 .. 100.0 |
| `dpad_x_pct` | `TimestampedFloat` | D-pad horizontal | -100.0 / 0.0 / 100.0 |
| `dpad_y_pct` | `TimestampedFloat` | D-pad vertical | -100.0 / 0.0 / 100.0 |
| `joystick_lt_pct` | `TimestampedFloat` | Left trigger | 0.0 / 100.0 |
| `joystick_rt_pct` | `TimestampedFloat` | Right trigger | 0.0 / 100.0 |
| `button_state_change` | `TimestampedInt` | Button event | 1 (pressed) / 0 (released) |

A liveliness token is declared on the source-base (default: the controller
name) for the lifetime of the session — same convention as the rest of the
Keelson connectors.

Key pattern:

```
{realm}/@v0/{entity_id}/pubsub/{subject}/<controller_id>/<input_name>
# e.g.: rise/@v0/rov/pubsub/joystick_x_pct/ssrov/joystick_x_pct
# e.g.: rise/@v0/rov/pubsub/button_state_change/ssrov/arm
```

## Quick Start

### Linux (direct device access)

```bash
hc2keelson -r rise -e rov --device /dev/input/js0 --controller ssrov
```

### macOS / Windows / Linux (cross-platform via relay)

The relay runs on the **host** (not in a container) and forwards events to
the containerised connector over TCP. Start one relay per controller, then
start the connector(s).

```bash
# 1. On the host — one terminal per controller
uv run --with pygame-ce scripts/hid_relay.py --port 9090                              # SSROV
uv run --with pygame-ce scripts/hid_relay.py --no-mfi --port 9091 --joystick-index 0  # Logitech (--no-mfi on macOS)

# 2. Run the connector(s) against the host relay
docker run --rm --network host ghcr.io/rise-maritime/keelson:latest \
    hc2keelson -r rise -e rov --controller ssrov \
    --relay host.docker.internal:9090
```

> **macOS note** — Apple's GCController framework exclusively claims known
> gamepads (e.g. Logitech F310), hiding them from SDL. `--no-mfi` disables
> the MFI backend and forces SDL to use IOKit. Run the SSROV relay
> **without** `--no-mfi`; SDL on IOKit doesn't deliver SSROV events.

The relay is also runnable as a PEP 723 script — `uv run scripts/hid_relay.py …`
resolves `pygame-ce` automatically without a separate venv.

## Profiles

Axis / button mappings live in [`profiles/`](profiles/) as YAML files. The
two bundled profiles (`ssrov.yaml`, `logitech.yaml`) are baked into the
container image under `/usr/local/share/hc-profiles/`.

To add a controller, drop a YAML file in `profiles/` (use `--controller
<name>`) or mount one anywhere and select it with `--controller-config
/path/to/file.yaml`. The `HC_PROFILES_DIR` env var overrides the search
path.

Required keys: `axis_map` and `button_name_map` (both `int -> str`).
Optional: `button_to_axis` (digital triggers published as axis values),
`shift_button` (button index acting as a modifier), `shift_map`
(shifted-name overrides while shift is held). See
[`profiles/ssrov.yaml`](profiles/ssrov.yaml) for the canonical example.

## CLI

```
Arguments:
  -r, --realm REALM              Keelson realm (default: "rise")
  -e, --entity-id ENTITY_ID      Entity id (default: "rov")
  --source-id ID                 Override source-id base (default: --controller value)

Device:
  -d, --device PATH              Joystick device path (default: /dev/input/js0)
  --relay HOST:PORT              TCP relay address for cross-platform mode
                                 (e.g. host.docker.internal:9090)
  --relay-max-retries N          Max connect attempts before exit
                                 (default: 0 = unlimited; backoff 1s..30s)
  -c, --controller NAME          Built-in profile name (default: ssrov)
  --controller-config PATH       Custom profile YAML; overrides --controller

Rate limiting (per-axis):
  --axis-min-interval-ms MS      Skip publish if last publish was <MS ago
                                 AND value barely changed (default: 30)
  --axis-min-change PCT          Percentage-point change that forces an
                                 immediate publish (default: 1.0)
  --axis-center-snap-pct PCT     Snap |value| < PCT to exactly 0.0 before
                                 rate-limiting (default: 0.0; recommended 2.0)

Zenoh:
  -m, --mode {peer,client}       Zenoh session mode
  --connect ENDPOINT             Zenoh router endpoint (repeatable)

Logging:
  -l, --log-level LEVEL          10=DEBUG, 20=INFO, 30=WARN (default: 20)
  --log-json                     One JSON object per line
```

Axis and D-pad events log at **DEBUG** (10); button events log at **INFO**
(20). At the default INFO level you'll only see button press/release lines,
even though axes and D-pad are publishing normally — run with
`--log-level 10` to verify joystick / D-pad activity.

## HID wire format

8 bytes, little-endian (`struct IhBB`) — identical to the Linux joystick
API and re-used over the TCP relay.

| Field | Size | Type | Description |
|-------|------|------|-------------|
| `timestamp` | 4 | uint32 | ms since device opened |
| `value` | 2 | int16 | -32768..32767 (axes) or 0/1 (buttons) |
| `type` | 1 | uint8 | `0x01`=button, `0x02`=axis, `0x80`=init flag |
| `number` | 1 | uint8 | button/axis index |

Events with the INIT flag are skipped so subscribers don't see stale state
on startup. Axes are normalised to ±100 percent (32768 divisor, clamped).

## Testing

```bash
uv run pytest -vv connectors/hand_controller/tests/
```

Manual verification with a controller attached:

```bash
uv run --with pygame-ce connectors/hand_controller/scripts/hid_relay.py --list
```

## Troubleshooting

**Controller not detected** — `ls -l /dev/input/js*` (Linux) or `uv run
--with pygame-ce scripts/hid_relay.py --list` (any OS). On Linux,
permissions may need `sudo chmod a+r /dev/input/js0` or `sudo usermod -a -G
input $USER` (re-login afterwards).

**No events received** — controller powered? Check `lsmod | grep joydev`
on Linux. Move sticks and press buttons; if buttons log but sticks don't,
that's expected at INFO — see the CLI note above.

**Relay connection refused** — nothing is listening on the relay port on
the host. Start `hid_relay.py` on the host **before** `docker run …`.
Verify with `lsof -nP -iTCP:9090 -sTCP:LISTEN`. The container reaches the
host via `host.docker.internal`, which works automatically on Docker Desktop
and is provided via `--add-host=host.docker.internal:host-gateway` on Linux.

## References

- [Linux joystick API](https://www.kernel.org/doc/Documentation/input/joystick-api.txt)
- [Keelson](https://rise-maritime.github.io/keelson/) — protocol docs
- [Zenoh](https://zenoh.io/) — underlying pub/sub
- [uv](https://docs.astral.sh/uv/) — Python package manager
