# iso22133 connector

Bridges an **ISO 22133** test object's MONR stream onto keelson.

```
iso22133_2keelson --realm rise --entity-id testobj-01 --source-id iso22133 \
    --monr-port 53240 --origin-lat 57.7731 --origin-lon 12.7708

  listens    UDP :53240 for MONR
  publishes  rise/@v0/testobj-01/pubsub/test_object_status/iso22133
             rise/@v0/testobj-01/pubsub/location_fix/iso22133
             rise/@v0/testobj-01/pubsub/speed_over_ground_knots/iso22133
             rise/@v0/testobj-01/pubsub/heading_true_north_deg/iso22133
```

## Monitoring only, deliberately

ISO 22133's command path — `OSTM` state transitions, `STRT`, and the
control-centre heartbeat (`HEAB`) whose loss obliges an object to abort — is
**not implemented**. That is a decision, not an omission.

The heartbeat is a safety contract with two ends. keelson has no heartbeat
subject and no vessel implements abort-on-loss, so shipping the commanding half
would put controls in front of an operator that look like they stop a test
object and do not.

Consequence, stated plainly: a real object generally will not stream MONR to a
centre that never completes a session with it. This bridge therefore suits an
object already running against a real control centre (ATOS), where it observes
the MONR stream — or the simulator in `tools/`.

## Which codec

RI-SE maintains the reference implementation at
https://github.com/RI-SE/iso22133, with SWIG Python bindings. The connector
**prefers those bindings** when importable and logs which codec is in use at
startup.

When they are absent it falls back to a built-in MONR decoder. That decoder is
not guesswork — every field, width, order and scale factor is cited against the
reference headers in `codec.py` — but it handles MONR only and refuses any other
message rather than half-parsing it.

Building the bindings needs CMake and SWIG; the library is source-only, not on
PyPI.

## Positions need an origin

MONR carries `x/y/z` in millimetres relative to the test-area origin, configured
on the object via OSEM. There is nothing in MONR to infer it from, so
`--origin-lat/--origin-lon` are required to publish a `location_fix`. Without
them the connector still publishes `test_object_status` and says why there is no
position — inventing an origin would place the object off West Africa.

`--yaw-offset-deg` is the same argument for heading: ISO yaw is in the test
area's local frame, and its rotation to true north is a property of the site.

## Simulator

```
python tools/test_object_sim.py --host 127.0.0.1 --port 53240 --rate 5 --loop
```

Test scaffolding. Walks the real state machine, including one deliberately
illegal transition so the bridge's transition checking is exercised.

**It proves the plumbing, not interoperability** — simulator and bridge share a
codec, so a field in the wrong place would agree on both sides. Real interop
needs ATOS or a vendor object.

## Tests

```
PYTHONPATH=. python -m pytest tests/ -q
```

The full mapping to keelson's other state machines, and the list of what ISO
22133 requires that this does not do, is in Crowsnest's `docs/ISO-22133.md`.
