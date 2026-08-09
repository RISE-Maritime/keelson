#!/usr/bin/env python3
"""ISO 22133 test-object bridge: MONR on UDP -> keelson subjects.

    iso22133_2keelson --realm rise --entity-id testobj-01 --source-id iso22133 \
        --monr-port 53240 --origin-lat 57.7731 --origin-lon 12.7708

    publishes  rise/@v0/testobj-01/pubsub/test_object_status/iso22133
               rise/@v0/testobj-01/pubsub/location_fix/iso22133
               rise/@v0/testobj-01/pubsub/speed_over_ground_knots/iso22133
               rise/@v0/testobj-01/pubsub/heading_true_north_deg/iso22133

MONITORING ONLY, AND THAT IS A DESIGN DECISION, NOT AN OMISSION.
----------------------------------------------------------------
ISO 22133's control path — OSTM state transitions, STRT, and the control-centre
heartbeat (HEAB) whose loss obliges an object to abort — is deliberately absent.
This bridge listens; it never commands.

The reason is that the heartbeat is a safety contract with two ends. keelson has
no heartbeat subject, and no vessel on this network implements abort-on-loss.
Shipping the commanding half would put controls in front of an operator that
look like they stop a test object and do not. The gap is written up in
Crowsnest's docs/ISO-22133.md rather than papered over with a button.

Consequence, stated plainly: a real ISO 22133 object will generally not stream
MONR to a centre that never completes a session with it. This bridge therefore
suits an object already running against a real control centre (ATOS), where it
observes the MONR stream, or the simulator in tools/. Making it a full control
centre is the follow-up the document describes.

The position problem
--------------------
MONR carries x/y/z relative to the test-area origin, which is configured on the
object via OSEM — there is nothing in MONR to infer it from. `--origin-lat/lon`
is therefore REQUIRED to publish `location_fix`; without it the bridge still
publishes `test_object_status` and says once, at startup, why there is no
position. Inventing an origin would put the object off West Africa.
"""

import argparse
import logging
import socket
import time

import zenoh

import keelson
from keelson.payloads.TestObjectStatus_pb2 import TestObjectStatus
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.Primitives_pb2 import TimestampedFloat

from iso22133_connector.codec import (
    decode_monr,
    decode_error_flags,
    codec_name,
    DecodeError,
)
from iso22133_connector import states

logger = logging.getLogger("iso22133")

SUBJECT_STATUS = "test_object_status"
SUBJECT_FIX = "location_fix"
SUBJECT_SOG = "speed_over_ground_knots"
SUBJECT_HEADING = "heading_true_north_deg"


def build_status(monr, cc_status: int, now_ns: int) -> TestObjectStatus:
    """MONR -> TestObjectStatus. Pure, so the mapping is testable without a socket."""
    msg = TestObjectStatus()
    msg.timestamp.FromNanoseconds(now_ns)
    msg.state = monr.state
    msg.ready_to_arm = monr.ready_to_arm
    msg.control_centre_status = cc_status
    msg.error_bitmask_raw = monr.error_status
    msg.object_id = monr.transmitter_id

    flags = decode_error_flags(monr.error_status)
    msg.errors.abort_request = flags["abort_request"]
    msg.errors.outside_geofence = flags["outside_geofence"]
    msg.errors.bad_positioning_accuracy = flags["bad_positioning_accuracy"]
    msg.errors.engine_fault = flags["engine_fault"]
    msg.errors.battery_fault = flags["battery_fault"]
    msg.errors.other = flags["other"]
    msg.errors.sync_point_ended = flags["sync_point_ended"]
    msg.errors.vendor_specific = flags["vendor_specific"]
    return msg


def run(session: zenoh.Session, args) -> None:
    publishers = {}
    for subject in (SUBJECT_STATUS, SUBJECT_FIX, SUBJECT_SOG, SUBJECT_HEADING):
        key = keelson.construct_pubsub_key(
            base_path=args.realm,
            entity_id=args.entity_id,
            subject=subject,
            source_id=args.source_id,
        )
        publishers[subject] = session.declare_publisher(key)
        logger.info("Publishing %s on %s", subject, key)

    logger.info("MONR codec in use: %s", codec_name())
    if args.origin_lat is None or args.origin_lon is None:
        logger.warning(
            "No --origin-lat/--origin-lon given: MONR carries test-area local "
            "coordinates only, so no location_fix will be published. "
            "test_object_status is unaffected.",
        )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.monr_host, args.monr_port))
    sock.settimeout(1.0)
    logger.info("Listening for MONR on %s:%d", args.monr_host, args.monr_port)

    previous_state = None
    decode_failures = 0

    while True:
        try:
            data, peer = sock.recvfrom(4096)
        except socket.timeout:
            continue

        try:
            monr = decode_monr(data)
        except DecodeError as exc:
            decode_failures += 1
            # Log the first few and then go quiet: a misconfigured port pointed
            # at an unrelated stream would otherwise fill the disk.
            if decode_failures <= 5:
                logger.warning("Ignoring %d bytes from %s: %s", len(data), peer, exc)
                if decode_failures == 5:
                    logger.warning(
                        "Further decode failures will be counted, not logged"
                    )
            continue

        now_ns = time.time_ns()

        if previous_state is not None and not states.is_legal_transition(
            previous_state, monr.state
        ):
            # A finding about the object under test, not about this bridge.
            logger.warning(
                "Object %d made a transition ISO 22133 does not permit: %s -> %s",
                monr.transmitter_id,
                states.state_name(previous_state),
                states.state_name(monr.state),
            )
        if previous_state != monr.state:
            logger.info(
                "Object %d state %s -> %s",
                monr.transmitter_id,
                (
                    states.state_name(previous_state)
                    if previous_state is not None
                    else "(first)"
                ),
                states.state_name(monr.state),
            )
        previous_state = monr.state

        status = build_status(monr, args.control_centre_status, now_ns)
        publishers[SUBJECT_STATUS].put(
            keelson.enclose(status.SerializeToString(), enclosed_at=now_ns)
        )

        if (
            args.origin_lat is not None
            and args.origin_lon is not None
            and monr.x_m is not None
            and monr.y_m is not None
        ):
            lat, lon = states.enu_to_wgs84(
                args.origin_lat, args.origin_lon, monr.x_m, monr.y_m
            )
            fix = LocationFix()
            fix.timestamp.FromNanoseconds(now_ns)
            fix.latitude = lat
            fix.longitude = lon
            if monr.z_m is not None:
                fix.altitude = monr.z_m + args.origin_alt
            publishers[SUBJECT_FIX].put(
                keelson.enclose(fix.SerializeToString(), enclosed_at=now_ns)
            )

        sog = states.speed_to_knots(monr.longitudinal_speed_mps)
        if sog is not None:
            msg = TimestampedFloat()
            msg.timestamp.FromNanoseconds(now_ns)
            msg.value = sog
            publishers[SUBJECT_SOG].put(
                keelson.enclose(msg.SerializeToString(), enclosed_at=now_ns)
            )

        heading = states.heading_from_yaw(monr.yaw_deg)
        if heading is not None:
            msg = TimestampedFloat()
            msg.timestamp.FromNanoseconds(now_ns)
            msg.value = (heading + args.yaw_offset_deg) % 360.0
            publishers[SUBJECT_HEADING].put(
                keelson.enclose(msg.SerializeToString(), enclosed_at=now_ns)
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="iso22133_2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__,
    )
    parser.add_argument("--log-level", type=int, default=logging.INFO)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, default="iso22133")
    parser.add_argument("--monr-host", type=str, default="0.0.0.0")
    parser.add_argument("--monr-port", type=int, default=53240)
    parser.add_argument(
        "--origin-lat",
        type=float,
        default=None,
        help="Test-area origin latitude; required for location_fix",
    )
    parser.add_argument(
        "--origin-lon",
        type=float,
        default=None,
        help="Test-area origin longitude; required for location_fix",
    )
    parser.add_argument("--origin-alt", type=float, default=0.0)
    parser.add_argument(
        "--yaw-offset-deg",
        type=float,
        default=0.0,
        help="Rotation from the test-area frame to true north",
    )
    parser.add_argument(
        "--control-centre-status",
        type=int,
        default=states.CC_READY,
        help="Reported alongside the object state; this bridge does not command",
    )
    parser.add_argument("--connect", type=str, action="append", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=args.log_level,
    )

    conf = zenoh.Config()
    if args.connect:
        conf.insert_json5("connect/endpoints", str(args.connect).replace("'", '"'))

    with zenoh.open(conf) as session:
        try:
            run(session, args)
        except KeyboardInterrupt:
            logger.info("Interrupted, closing")


if __name__ == "__main__":
    main()
