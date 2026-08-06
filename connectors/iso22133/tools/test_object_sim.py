#!/usr/bin/env python3
"""A synthetic ISO 22133 test object, streaming MONR over UDP.

TEST SCAFFOLDING, NOT A CONNECTOR. There is no ISO 22133 object on this network,
so without something to talk to the bridge cannot be exercised at all. This
walks the real state machine and emits real MONR frames so the bridge, the
keelson subject and the Crowsnest view can be verified end to end.

    python tools/test_object_sim.py --host 127.0.0.1 --port 53240 --rate 10

WHAT THIS PROVES, AND WHAT IT DOES NOT
--------------------------------------
It proves the plumbing: framing, decode, state handling, the keelson publish
path, and the UI. It does NOT prove wire-level interoperability with a
third-party object, because the simulator and the bridge share one codec — if
that codec has a field in the wrong place, both agree and the test still passes.
Real interop needs ATOS or a vendor object on the other end. Said plainly here
so nobody mistakes a green run for a conformance result.

The sequence deliberately includes an ILLEGAL transition near the end
(RUNNING -> ARMED) so the bridge's transition checking is exercised by
something other than its own unit tests.
"""

import argparse
import math
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iso22133_connector.codec import encode_monr  # noqa: E402
from iso22133_connector import states  # noqa: E402

# (state, seconds to hold, description)
SEQUENCE = [
    (states.INIT, 3, "powering up"),
    (states.DISARMED, 4, "idle, safe"),
    (states.PRE_ARMING, 3, "arming checks"),
    (states.ARMED, 4, "armed, holding"),
    (states.PRE_RUNNING, 2, "about to start"),
    (states.RUNNING, 12, "executing the test"),
    (states.POSTRUN, 4, "test complete"),
    (states.DISARMED, 3, "back to safe"),
    (states.ARMED, 3, "ILLEGAL from DISARMED without PRE_ARMING"),
    (states.ABORTING, 4, "abort"),
    (states.DISARMED, 3, "recovered"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=53240)
    ap.add_argument("--rate", type=float, default=10.0, help="MONR rate in Hz")
    ap.add_argument("--object-id", type=int, default=1)
    ap.add_argument("--loop", action="store_true", help="repeat the sequence")
    ap.add_argument("--speed", type=float, default=4.0, help="m/s while RUNNING")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1.0 / max(args.rate, 0.1)
    counter = 0
    x = y = 0.0
    heading_deg = 45.0

    print(f"streaming MONR to {args.host}:{args.port} at {args.rate} Hz "
          f"as object {args.object_id}")

    while True:
        for state, hold_s, description in SEQUENCE:
            print(f"  {states.state_name(state):<18} {description}")
            ticks = max(1, int(hold_s / period))
            for _ in range(ticks):
                moving = state == states.RUNNING
                speed = args.speed if moving else 0.0
                if moving:
                    x += speed * period * math.sin(math.radians(heading_deg))
                    y += speed * period * math.cos(math.radians(heading_deg))
                    heading_deg = (heading_deg + 12.0 * period) % 360.0

                # Error bits that tell a story: the object reports its abort,
                # and drifts outside the geofence during the run.
                error_status = 0
                if state == states.ABORTING:
                    error_status |= 0x80          # ABORT_REQUEST
                if moving and math.hypot(x, y) > 40.0:
                    error_status |= 0x40          # OUTSIDE_GEOFENCE

                ready = (
                    states.READY_TO_ARM
                    if state in (states.DISARMED, states.PRE_ARMING, states.ARMED)
                    else states.NOT_READY_TO_ARM
                )

                frame = encode_monr(
                    transmitter_id=args.object_id,
                    message_counter=counter,
                    gps_qms_of_week=int(time.time() * 4) % (7 * 24 * 3600 * 4),
                    x_m=x, y_m=y, z_m=0.0,
                    yaw_deg=heading_deg,
                    pitch_deg=0.0, roll_deg=0.0,
                    longitudinal_speed_mps=speed, lateral_speed_mps=0.0,
                    longitudinal_acc_mps2=0.0, lateral_acc_mps2=0.0,
                    drive_direction=0, state=state, ready_to_arm=ready,
                    error_status=error_status, error_code=0,
                )
                sock.sendto(frame, (args.host, args.port))
                counter += 1
                time.sleep(period)

        if not args.loop:
            print("sequence complete")
            return

        # Power down before restarting at INIT. Without this the loop seam is
        # DISARMED -> INIT, which the standard does not permit, and the bridge
        # correctly reports it — a violation the simulator invented rather than
        # the object committing. Scaffolding that manufactures false findings
        # teaches operators to ignore findings.
        print("  looping — powering down first so the seam is legal")
        for _ in range(max(1, int(1.0 / period))):
            sock.sendto(
                encode_monr(
                    transmitter_id=args.object_id, message_counter=counter,
                    x_m=x, y_m=y, z_m=0.0, yaw_deg=heading_deg,
                    longitudinal_speed_mps=0.0,
                    state=states.OFF, ready_to_arm=states.NOT_READY_TO_ARM,
                ),
                (args.host, args.port),
            )
            counter += 1
            time.sleep(period)


if __name__ == "__main__":
    main()
