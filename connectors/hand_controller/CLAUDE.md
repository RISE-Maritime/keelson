# CLAUDE.md — hand_controller connector

Joystick / gamepad → Keelson connector. Reads 8-byte Linux joystick HID
events (either directly from `/dev/input/jsX` or from a cross-platform TCP
relay) and publishes axes + buttons to the standard Keelson subjects
(`joystick_*_pct`, `dpad_*_pct`, `button_state_change`).

Supported controllers via `--controller`: `ssrov` (Seascape ROV Hand
Controller — default), `logitech` (Logitech F310/F710 in DirectInput mode).
Custom profiles via `--controller-config <path-to-yaml>`.

## Layout

```
bin/
  hc2keelson.py        Single self-contained entry point — published as
                       /usr/local/bin/hc2keelson. HID event constants,
                       CLI parsing, and runtime are all inlined here.
profiles/
  ssrov.yaml           Default profile
  logitech.yaml
scripts/
  hid_relay.py         HOST-SIDE relay (pygame-ce). Not containerised.
                       PEP 723 self-contained; run via `uv run scripts/hid_relay.py`.
tests/
  conftest.py          SourceFileLoader fixture for bin/hc2keelson.py
  test_hc2keelson.py
doc/                   Datasheets (kept here, not under repo-level docs/).
```

### Why no helper modules in `bin/`?

The `docker/Dockerfile` copies every connector's `bin/*.py` flat into
`/usr/local/bin/`, then strips the `.py` extension only from entry-point
scripts (the ones with `if __name__ == "__main__"`). Helper modules with
generic names (`terminal_inputs.py`, etc.) would silently overwrite each
other across connectors with no build-time warning. Until the monorepo
adopts a proper per-connector namespacing scheme, hand_controller keeps
everything in a single entry-point file. **Don't reintroduce sibling
modules in `bin/` here without solving the collision problem first.**

## HID wire format (8 bytes, struct `IhBB`)

| Field     | Size | Type   | Description                                |
|-----------|------|--------|--------------------------------------------|
| timestamp | 4    | uint32 | ms since device opened                     |
| value     | 2    | int16  | -32768..32767 (axes) or 0/1 (buttons)      |
| type      | 1    | uint8  | 0x01=button, 0x02=axis, 0x80=init flag    |
| number    | 1    | uint8  | button / axis index                        |

Init-flagged events (`type & 0x80`) are skipped — they would otherwise
publish stale state on every device open.

## Profile schema

YAML with required keys `axis_map` (`int -> str` subject name) and
`button_name_map` (`int -> str` source-id suffix). Optional:

- `button_to_axis`: digital triggers published as axis values (0.0 / 100.0)
- `shift_button`: button index that acts as a modifier while held
- `shift_map`: `{button -> name}` overrides published when shift is held

Loader (`load_profile` in `bin/hc2keelson.py`) takes a name **or** a path.
Search order: `HC_PROFILES_DIR` → `<repo>/profiles/` → `/usr/local/share/hc-profiles/`.
The container ships profiles at `/usr/local/share/hc-profiles/` (copied by `docker/Dockerfile`).

## Cross-platform relay

```
[Controller] → [pygame on HOST] → TCP:9090 → [Docker container] → Zenoh/Keelson
```

Docker Desktop on macOS / Windows can't pass USB through to containers, so
`scripts/hid_relay.py` runs on the host (pygame-ce, IOKit / DirectInput /
evdev under the hood) and forwards 8-byte events over TCP. The wire format
is identical to the Linux joystick API — the container code path is the
same for direct device and relay sources.

On macOS, the Logitech F310 needs `--no-mfi` to bypass Apple's
GCController exclusive claim. The SSROV is read via IOKit and must run
**without** `--no-mfi`.

## Design notes worth knowing

- **Publisher caching** — `PUBLISHERS` dict caches Zenoh publishers per
  `(realm, entity_id, subject, source_id)` to avoid re-declaring on every
  event.
- **Per-axis rate limit** — `--axis-min-interval-ms` + `--axis-min-change`
  skip publishes when the axis barely moved AND the previous publish was
  very recent. `--axis-center-snap-pct` snaps near-zero values to exact 0
  to clean up the joystick ADC rest offset (without it a released stick
  publishes its residual `-0.39` % and the rate limit freezes that in).
- **TCP relay buffer drain** — `event_source_tcp` parses all buffered
  bytes per recv and keeps only the latest axis event per `(type, number)`
  while preserving full button event order. Prevents axis lag during fast
  movement.
- **Liveliness, not custom health** — declares `declare_liveliness_token`
  at session open. No bespoke `controller_health` subject (deliberately —
  liveliness + `entity_health` cover the signal).

## Skip-list — things to NOT add back

- `controller_health` pub/sub subject (use liveliness + `entity_health`).
- Per-connector Dockerfile / docker-compose (the all-in-one `docker/Dockerfile` builds it).
- `skarv` dependency (not used in code — was carried over from a template).
- `environs` dependency (also unused).
- `bin/hc2keelson` without `.py` extension (project convention: keep the
  extension, the monorepo Dockerfile strips it for entry points).
