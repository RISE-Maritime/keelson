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
- **Per-axis publish-rate bounds (paired).** `--axis-min-hz` (default 10)
  is the floor enforced by a backstop thread; `--axis-max-hz` (default 50)
  is the ceiling enforced by the rate limiter in `handle_joystick_event`;
  `--axis-deadband-pct` (default 1.0) is the change that always bypasses
  the ceiling. `terminal_inputs()` validates `min_hz <= max_hz`. The
  rate cap only applies to *change-driven* publishes — the backstop fires
  unconditionally, so pushing min_hz above max_hz would invert that
  contract.
- **`_axis_last_known` vs `_axis_last_published`.** Two state dicts with
  different jobs. `_axis_last_known` is the canonical "what is the stick
  at right now" view — updated on every observed axis event, including
  INIT and including events the rate limiter suppresses. The backstop
  reads from here so it always sees the freshest value, never a
  rate-limit-stale one. `_axis_last_published` is publish bookkeeping for
  the rate limiter: it tracks "what did we last actually send on the
  wire, and when." A subtle but important split; don't collapse them.
- **INIT semantics differ by event type.** For axes, INIT-flagged events
  flow through normally — they're the kernel's bootstrap snapshot and
  exactly what a late joiner needs to see. For buttons they're dropped
  outright, and must not mutate `_shift_held` either (acting on a stale
  "shift is held" snapshot would corrupt the modifier state). Captured
  via `is_init = bool(event_type & JS_EVENT_INIT)` *before* stripping
  the flag.
- **Relay synthesises the INIT burst** on every new client connection.
  pygame has no INIT concept, so `scripts/hid_relay.py:_emit_init_burst`
  iterates `js.get_axis(i)` / `get_button(i)` / `get_hat(0)` and packs
  them as `JS_EVENT_* | 0x80`. Mirrors the Linux kernel's bootstrap
  burst so both modes give the container identical initial state. A
  fresh client reconnect is treated as a fresh device open.
- **`--axis-center-snap-pct`** snaps near-zero values to exact 0 to clean
  up the joystick ADC rest offset (without it a released stick publishes
  its residual `-0.39` % and the rate cap freezes that in).
- **TCP relay buffer drain** — `event_source_tcp` parses all buffered
  bytes per recv and keeps only the latest axis event per `(type, number)`
  while preserving full button event order. Prevents axis lag during fast
  movement.
- **Liveliness, not custom health** — declares `declare_liveliness_token`
  at session open. No bespoke `controller_health` subject (deliberately —
  liveliness + `entity_health` cover the signal). The axis backstop is
  about *state recovery*, not aliveness; don't conflate the two.

## Skip-list — things to NOT add back

- `controller_health` pub/sub subject (use liveliness + `entity_health`).
- Per-connector Dockerfile / docker-compose (the all-in-one `docker/Dockerfile` builds it).
- `skarv` dependency (not used in code — was carried over from a template).
- `environs` dependency (also unused).
- `bin/hc2keelson` without `.py` extension (project convention: keep the
  extension, the monorepo Dockerfile strips it for entry points).
- The old `--axis-min-interval-ms` / `--axis-min-change` flag pair (renamed
  to `--axis-max-hz` / `--axis-deadband-pct` so they pair obviously with
  the new `--axis-min-hz` floor; units unified to Hz).
