"""A Rover on TCP that may be keyed for MAVLink 2 signing, for test_mavlink_signing.py.

Its own process, because pymavlink's dialect choice is process-global: MAVLink 2 here must not leak into
the tests that mock a 1.0 link. It heartbeats at 2 Hz and ACKs every COMMAND_LONG it ACCEPTS, printing
``CMD <command>`` for each. Keyed (``--key-file``), it signs what it sends and drops anything unsigned or
wrongly signed, as a keyed ArduPilot does on a telemetry port.
"""

import argparse
import os
import sys
import time

os.environ["MAVLINK20"] = "1"

from pymavlink import mavutil  # noqa: E402
from pymavlink.dialects.v20 import ardupilotmega as m  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, required=True)
p.add_argument("--key-file")
args = p.parse_args()

conn = mavutil.mavlink_connection(
    f"tcpin:127.0.0.1:{args.port}", source_system=1, source_component=1
)
if args.key_file:
    key = bytes.fromhex(open(args.key_file).read().strip())
    conn.setup_signing(
        key, sign_outgoing=True, allow_unsigned_callback=lambda *_: False, link_id=0
    )
print("READY", flush=True)
next_hb = 0.0
while True:
    now = time.monotonic()
    if now >= next_hb:
        try:
            conn.mav.heartbeat_send(
                m.MAV_TYPE_SURFACE_BOAT,
                m.MAV_AUTOPILOT_ARDUPILOTMEGA,
                0,
                0,
                m.MAV_STATE_STANDBY,
            )
        except OSError:
            pass  # no client yet
        next_hb = now + 0.5
    msg = conn.recv_match(type="COMMAND_LONG", blocking=True, timeout=0.1)
    if msg is not None:
        print(f"CMD {msg.command}", flush=True)
        conn.mav.command_ack_send(msg.command, m.MAV_RESULT_ACCEPTED)
    sys.stdout.flush()
