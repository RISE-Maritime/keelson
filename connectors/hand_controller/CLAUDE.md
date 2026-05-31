# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Keelson/Zenoh connector that reads from joystick/gamepad controllers and publishes real-time HID events to the Keelson maritime protocol as protobuf `TimestampedInt` messages. Supports Linux direct device access (`/dev/input/js0`) and cross-platform TCP relay mode (macOS/Windows/Linux).

**Supported controllers:** Seascape ROV Hand Controller (`--controller ssrov`, default) and Logitech F310/F710 (`--controller logitech`).

## Commands

**Linting:**
```bash
black --check bin/*
pylint bin/*
black bin/*          # auto-format
```

**Testing (manual, requires joystick device):**
```bash
python examples/joystick_reader.py --list       # list available devices
python examples/joystick_reader.py              # read joystick events live
```

**Run the connector (Linux direct mode):**
```bash
python bin/hc2keelson --realm rise --entity-id rov
docker compose -f docker-compose.hc.yml up
```

**Run the connector (cross-platform relay mode — macOS/Windows/Linux):**
```bash
# 1. On the host machine (requires pygame-ce):
uv pip install --system -r requirements_relay.txt

# 2a. SSROV relay:
uv run bin/hid_relay.py --port 9090

# 2b. Logitech relay (--no-mfi needed on macOS):
uv run bin/hid_relay.py --no-mfi --port 9091 --joystick-index 0

# 3. Start the containers:
docker compose -f docker-compose.hc.yml --profile ssrov --profile logitech up

# List available joysticks on host:
uv run bin/hid_relay.py --list              # shows SSROV
uv run bin/hid_relay.py --no-mfi --list     # shows SSROV + Logitech
```

## Architecture

**Main script:** [bin/hc2keelson](bin/hc2keelson) — reads 8-byte HID events from `/dev/input/js0` and publishes to Zenoh/Keelson.

**Argument parsing:** [bin/terminal_inputs.py](bin/terminal_inputs.py) — CLI args (`-r/--realm`, `-e/--entity-id`, `-d/--device`, `--relay`, `-c/--controller`, `-m/--mode`, `--connect`, `-l/--log-level`).

**Host-side relay:** [bin/hid_relay.py](bin/hid_relay.py) — cross-platform joystick reader using pygame, forwards events over TCP to the containerized connector.

### HID Event Format (8 bytes, struct `IhBB`)
| Field | Size | Type | Description |
|-------|------|------|-------------|
| timestamp | 4 bytes | uint32 | ms since device opened |
| value | 2 bytes | int16 | -32768–32767 (axes) or 0/1 (buttons) |
| type | 1 byte | uint8 | 0x01=button, 0x02=axis, 0x80=init flag |
| number | 1 byte | uint8 | button/axis index |

### Controller Profiles (`--controller`)

Axis/button mappings are defined per controller in `CONTROLLER_PROFILES`. The SSROV profile uses ROV-function naming with shift modifier logic. The Logitech profile uses hardware-descriptive naming with no shift logic — a downstream control manager maps physical inputs to vessel functions.

**SSROV** (`--controller ssrov`): `joystick_x/y/z/rz` axes, ROV-function button names (`arm`, `lights1_brighter`, etc.), shift modifier on button 9.

**Logitech F310/F710** (`--controller logitech`, DirectInput mode, switch on "D"): `left_stick_x/y`, `right_stick_x/y` axes; `button_a/b/x/y`, `button_lb/rb`, `button_lt/rt`, `button_back/start`, `button_l3/r3` buttons. No shift logic. On macOS, the relay needs `--no-mfi` flag.

Unmapped buttons publish to `button_state_change` with the button number as the function name in the source-id.

### Keelson Key Expression Pattern
```
{realm}/@v0/{entity_id}/pubsub/{subject}/<controller_id>/<input_name>
# e.g.: rise/@v0/rov/pubsub/joystick_x_pct/ssrov/joystick_x_pct
# e.g.: rise/@v0/rov/pubsub/button_state_change/ssrov/arm
```

### Cross-Platform Relay Architecture

```
[Controller] → [pygame on HOST] → TCP:9090 → [Docker container] → Zenoh/Keelson
```

- **Host relay** (`bin/hid_relay.py`): reads joystick via pygame (macOS/Windows/Linux), sends 8-byte events over TCP
- **Container connector** (`bin/hc2keelson --relay host:port`): receives events from TCP instead of device file
- **Wire protocol**: identical 8-byte `IhBB` format as Linux joystick API — no translation needed
- **`host.docker.internal`**: resolves to host from Docker Desktop; on Linux Docker Engine use `extra_hosts` (included in compose)

### Key Design Decisions
- **Joystick interface over serial**: kernel-driven events are much faster than the legacy `/dev/ttyACM1` serial at 5-second polling intervals
- **Publisher caching**: `get_or_create_publisher()` lazily creates and caches Zenoh publishers to avoid re-instantiation per event
- **Init events skipped**: events with type `& 0x80` (JS_EVENT_INIT) are ignored to avoid publishing stale state on startup
- **1ms sleep**: prevents busy-waiting without missing events
- **TCP relay for cross-platform**: Docker Desktop (macOS/Windows) cannot pass USB devices to containers, so the host reads the joystick and relays events over TCP

## Dependencies

- `keelson==0.5.1` — Keelson protocol (Zenoh + protobuf)
- `skarv==0.3.0` — in-memory data vault
- `environs==15.0.1` — env var management
- Dev: `black==25.9.0`, `pylint==4.0.2`
- Host relay: `pygame>=2.5.0` (see `requirements_relay.txt`, only needed on host for cross-platform mode)

## Docker

Base image: `python:3.13-slim-bookworm` with tini for signal handling. The compose file defines two profiles:

- **`ssrov`** (`keelson-connector-ssrov`): Cross-platform relay mode for the Seascape controller, connects to host-side `hid_relay.py` on port 9090
- **`logitech`** (`keelson-connector-logitech`): Cross-platform relay mode for Logitech F310/F710, connects to host-side `hid_relay.py` on port 9091

Both services use `network_mode: host` and include `extra_hosts` for `host.docker.internal` compatibility on Linux Docker Engine.
