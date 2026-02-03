#!/usr/bin/env python3

"""
Camera connector for Keelson.

Captures video frames from any OpenCV-compatible source (RTSP, USB, file, etc.)
and publishes them as raw or compressed images on the Zenoh bus.
"""

import time
import logging
import datetime
import argparse
from collections import deque
from threading import Thread, Event

import cv2
import numpy
import zenoh

import keelson
from keelson.payloads.foxglove.CompressedImage_pb2 import CompressedImage
from keelson.payloads.foxglove.RawImage_pb2 import RawImage
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
)

SUPPORTED_FORMATS = ["jpeg", "webp", "png"]
MCAP_TO_OPENCV_ENCODINGS = {"jpeg": ".jpg", "webp": ".webp", "png": ".png"}

logger = logging.getLogger("camera2keelson")


def run(session, args):
    """Run the camera capture and publish loop."""

    # Camera COMPRESSED IMAGE publisher
    keyexp_comp = keelson.construct_pubsub_key(
        base_path=args.realm,
        entity_id=args.entity_id,
        subject="image_compressed",
        source_id=args.source_id,
    )
    pub_camera_comp = session.declare_publisher(
        keyexp_comp,
        priority=zenoh.Priority.INTERACTIVE_HIGH,
        congestion_control=zenoh.CongestionControl.DROP,
    )
    logger.info(f"Created publisher: {keyexp_comp}")

    # Camera RAW IMAGE publisher
    keyexp_raw = keelson.construct_pubsub_key(
        base_path=args.realm,
        entity_id=args.entity_id,
        subject="image_raw",
        source_id=args.source_id,
    )
    pub_camera_raw = session.declare_publisher(
        keyexp_raw,
        priority=zenoh.Priority.INTERACTIVE_HIGH,
        congestion_control=zenoh.CongestionControl.DROP,
    )
    logger.info(f"Created publisher: {keyexp_raw}")

    logger.info("Camera source: %s", args.camera_url)

    # Opening a VideoCapture object using the supplied url
    cap = cv2.VideoCapture(args.camera_url)
    fps = cap.get(cv2.CAP_PROP_FPS)
    logger.info("Native framerate of stream: %s", fps)
    buffer = deque(maxlen=1)
    close_down = Event()

    def _capturer():
        while cap.isOpened() and not close_down.is_set():
            ret, img = cap.read()
            ingress_timestamp = time.time_ns()

            if not ret:
                logger.error("No frames returned from the stream. Exiting!")
                return

            logger.info("Got new frame, at time: %d", ingress_timestamp)

            height, width, _ = img.shape
            logger.debug("with height: %d, width: %d", height, width)

            buffer.append((ingress_timestamp, img))

    # Start capture thread
    t = Thread(target=_capturer)
    t.daemon = True
    t.start()

    try:
        previous = time.time()
        while True:
            try:
                ingress_timestamp, img = buffer.pop()
            except IndexError:
                time.sleep(0.01)
                continue

            logger.debug("Processing raw frame")

            height, width, _ = img.shape
            data = img.tobytes()

            width_step = len(data) // height
            logger.debug(
                "Frame total byte length: %d, widthstep: %d", len(data), width_step
            )

            if args.send == "raw":
                logger.debug("Send RAW frame...")
                payload = RawImage()
                payload.timestamp.FromNanoseconds(ingress_timestamp)
                if args.frame_id is not None:
                    payload.frame_id = args.frame_id
                payload.width = width
                payload.height = height
                payload.encoding = "bgr8"  # Default in OpenCV
                payload.step = width_step
                payload.data = data

                serialized_payload = payload.SerializeToString()
                envelope = keelson.enclose(serialized_payload)
                pub_camera_raw.put(envelope)
                logger.debug(f"...published on {keyexp_raw}")

            if args.send in SUPPORTED_FORMATS:
                logger.debug(f"SEND {args.send} frame...")
                _, compressed_img = cv2.imencode(
                    MCAP_TO_OPENCV_ENCODINGS[args.send], img
                )
                compressed_img = numpy.asarray(compressed_img)
                data = compressed_img.tobytes()

                payload = CompressedImage()
                if args.frame_id is not None:
                    payload.frame_id = args.frame_id
                payload.data = data
                payload.format = args.send

                serialized_payload = payload.SerializeToString()
                envelope = keelson.enclose(serialized_payload)
                pub_camera_comp.put(envelope)
                logger.debug(f"...published on {keyexp_comp}")

            if args.save == "raw":
                logger.debug("Saving raw frame...")
                filename = f'{ingress_timestamp}_"bgr8".raw'
                cv2.imwrite(filename, img)

            if args.save in SUPPORTED_FORMATS:
                logger.debug(f"Saving {args.save} frame...")
                ingress_timestamp_seconds = ingress_timestamp / 1e9
                ingress_datetime = datetime.datetime.fromtimestamp(
                    ingress_timestamp_seconds
                )
                ingress_iso = ingress_datetime.strftime("%Y-%m-%dT%H%M%S-%fZ%z")
                filename = (
                    f"{args.save_path}/{ingress_iso}_{args.source_id}.{args.save}"
                )
                cv2.imwrite(filename, img)

            # Frame rate monitoring
            now = time.time()
            processing_frame_rate = now - previous
            previous = now

            logger.info(
                "Processing framerate: %.2f (%d%% of native)",
                processing_frame_rate,
                100 * (processing_frame_rate / fps) if fps else 0,
            )

    except KeyboardInterrupt:
        logger.info("Closing down on user request!")
    finally:
        logger.debug("Joining capturer thread...")
        close_down.set()
        t.join()
        logger.debug("Done!")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="camera2keelson",
        description="Capture video frames and publish to Keelson/Zenoh",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Common Zenoh arguments from scaffolding
    add_common_arguments(parser)

    # Camera-specific arguments
    parser.add_argument(
        "-r",
        "--realm",
        required=True,
        type=str,
        help="Unique id for a domain/realm to connect (e.g. rise)",
    )
    parser.add_argument(
        "-e",
        "--entity-id",
        required=True,
        type=str,
        help="Entity being a unique id representing an entity within the realm (e.g. landkrabba)",
    )
    parser.add_argument(
        "-s",
        "--source-id",
        required=True,
        type=str,
        help="Source identifier (e.g. camera/0)",
    )
    parser.add_argument(
        "-u",
        "--camera-url",
        type=str,
        required=True,
        help="RTSP URL or any other video source that OpenCV can handle",
    )
    parser.add_argument(
        "--send",
        choices=["raw", "webp", "jpeg", "png"],
        type=str,
        required=False,
        help="Format to publish frames in",
    )
    parser.add_argument(
        "--save",
        choices=["raw", "webp", "jpeg", "png"],
        type=str,
        required=False,
        help="Format to save frames to disk in",
    )
    parser.add_argument(
        "--save-path",
        default="./rec",
        type=str,
        required=False,
        help="Directory path to save frames to",
    )
    parser.add_argument(
        "-f",
        "--frame-id",
        type=str,
        default=None,
        required=False,
        help="Frame ID to include in image payloads",
    )

    args = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    logger.info("Starting camera2keelson")
    logger.info(f"Realm: {args.realm}")
    logger.info(f"Entity ID: {args.entity_id}")
    logger.info(f"Source ID: {args.source_id}")

    # Configure Zenoh using scaffolding
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    # Open Zenoh session
    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)
    logger.info("Zenoh session opened")

    try:
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            run(session, args)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        logger.info("Closing Zenoh session...")
        session.close()
        logger.info("Session closed")


if __name__ == "__main__":
    main()
