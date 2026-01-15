#!/usr/bin/env python3

"""
Command line utility tool for faking radar spokes
"""
import time
import atexit
import logging
import argparse

import numpy as np
import zenoh
import keelson
from keelson.payloads.RadarReading_pb2 import RadarSpoke, RadarSweep
from keelson_connectors_common import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
)

KEELSON_SUBJECT_RADAR_SPOKE = "radar_spoke"
KEELSON_SUBJECT_RADAR_SWEEP = "radar_sweep"


def run(session: zenoh.Session, args: argparse.Namespace):

    # Declaring zenoh publishers
    spoke_publisher = session.declare_publisher(
        keelson.construct_pubsub_key(
            base_path=args.realm,
            entity_id=args.entity_id,
            subject=KEELSON_SUBJECT_RADAR_SPOKE,
            source_id=args.source_id,
        ),
        priority=zenoh.Priority.INTERACTIVE_HIGH,
        congestion_control=zenoh.CongestionControl.DROP,
    )

    logging.info("Spokes will be published to: %s", spoke_publisher.key_expr)

    sweep_publisher = session.declare_publisher(
        keelson.construct_pubsub_key(
            base_path=args.realm,
            entity_id=args.entity_id,
            subject=KEELSON_SUBJECT_RADAR_SWEEP,
            source_id=args.source_id,
        ),
        priority=zenoh.Priority.INTERACTIVE_HIGH,
        congestion_control=zenoh.CongestionControl.DROP,
    )

    logging.info("Sweeps will be published to: %s", sweep_publisher.key_expr)

    logging.info("Starting to send spokes and sweeps!")

    time_per_spoke = args.seconds_per_sweep / args.spokes_per_sweep

    while True:
        sweep_start = time.time()

        sweep = RadarSweep()

        for ix in range(args.spokes_per_sweep):
            spoke = RadarSpoke()
            spoke.timestamp.FromNanoseconds(time.time_ns())

            # Zero relative position
            spoke.pose.position.x = 0
            spoke.pose.position.y = 0
            spoke.pose.position.z = 0

            # Identity quaternion
            spoke.pose.orientation.x = 0
            spoke.pose.orientation.y = 0
            spoke.pose.orientation.z = 0
            spoke.pose.orientation.w = 1

            spoke.azimuth = ix / args.spokes_per_sweep * 2 * np.pi

            spoke.range = args.spoke_range

            spoke.fields.add(name="intensity", offset=0, type=1)  # UINT8

            data = np.zeros(args.spoke_resolution, dtype=np.uint8)
            data[int(ix / args.spokes_per_sweep * args.spoke_resolution)] = 255

            spoke.data = data.tobytes()

            sweep.spokes.append(spoke)

            serialized_payload = spoke.SerializeToString()
            logging.debug("...serialized.")

            envelope = keelson.enclose(serialized_payload)
            logging.debug("...enclosed into envelope")

            spoke_publisher.put(envelope)
            logging.debug("...published to zenoh!")

            scheduled_finish_time = (ix + 1) * time_per_spoke
            while time.time() - sweep_start < scheduled_finish_time:
                time.sleep(10e-9)

        serialized_payload = sweep.SerializeToString()
        logging.debug("...serialized sweep.")

        envelope = keelson.enclose(serialized_payload)
        logging.debug("...enclosed into envelope")

        sweep_publisher.put(envelope)
        logging.debug("...published to zenoh!")

        logging.info("Sweep took: %s seconds", time.time() - sweep_start)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="fake_radar",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, required=True)
    parser.add_argument("--spokes_per_sweep", type=int, default=2048)
    parser.add_argument("--seconds_per_sweep", type=float, default=2)
    parser.add_argument("--spoke_resolution", type=int, default=512)
    parser.add_argument("--spoke_range", type=int, default=5000)

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

    # Construct session
    logging.info("Opening Zenoh session...")
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )
    session = zenoh.open(conf)

    def _on_exit():
        session.close()

    atexit.register(_on_exit)

    try:
        run(session, args)
    except KeyboardInterrupt:
        logging.info("Program ended due to user request (Ctrl-C)")
        pass
