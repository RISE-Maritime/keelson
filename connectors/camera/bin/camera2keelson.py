#!/usr/bin/env python3

"""
Camera connector for Keelson.

Captures video frames from any OpenCV-compatible source (RTSP, USB, file, etc.)
and publishes them as raw or compressed images on the Zenoh bus.
"""

import sys
import json
import time
import logging
import datetime
import argparse
from pathlib import Path
from collections import deque
from threading import Thread, Event

import cv2
import numpy
import zenoh
from jsonschema import validate, ValidationError

import keelson
from keelson.payloads.foxglove.CameraCalibration_pb2 import CameraCalibration
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

CALIBRATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "title": "Camera Calibration",
    "description": "Camera intrinsic calibration parameters (OpenCV/ROS pinhole model).",
    "required": ["width", "height"],
    "properties": {
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1},
        "distortion_model": {"type": "string"},
        "D": {"type": "array", "items": {"type": "number"}},
        "K": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 9,
            "maxItems": 9,
        },
        "R": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 9,
            "maxItems": 9,
        },
        "P": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 12,
            "maxItems": 12,
        },
    },
    "additionalProperties": False,
}

logger = logging.getLogger("camera2keelson")


def _build_calibration_payload(cal: dict, frame_id: str | None) -> bytes:
    """Build a serialized CameraCalibration protobuf from calibration data."""
    payload = CameraCalibration()
    payload.timestamp.FromNanoseconds(time.time_ns())
    if frame_id is not None:
        payload.frame_id = frame_id
    payload.width = cal["width"]
    payload.height = cal["height"]
    if "distortion_model" in cal:
        payload.distortion_model = cal["distortion_model"]
    payload.D[:] = cal.get("D", [])
    payload.K[:] = cal.get("K", [])
    payload.R[:] = cal.get("R", [])
    payload.P[:] = cal.get("P", [])
    return payload.SerializeToString()


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

    # Calibration publisher (periodic publishing happens in the main loop)
    pub_cal = None
    cal_data = getattr(args, "_calibration_data", None)
    if cal_data is not None:
        keyexp_cal = keelson.construct_pubsub_key(
            base_path=args.realm,
            entity_id=args.entity_id,
            subject="camera_calibration",
            source_id=args.source_id,
        )
        pub_cal = session.declare_publisher(keyexp_cal)
        logger.info("Created calibration publisher: %s", keyexp_cal)

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
        last_cal_publish = 0  # ensures first iteration publishes immediately
        while True:
            # Periodic calibration publishing
            now = time.time()
            if (
                pub_cal is not None
                and (now - last_cal_publish) >= args.calibration_interval
            ):
                pub_cal.put(
                    keelson.enclose(_build_calibration_payload(cal_data, args.frame_id))
                )
                logger.info("Published camera calibration")
                last_cal_publish = now

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
    parser.add_argument(
        "--calibration-file",
        type=Path,
        default=None,
        required=False,
        help="Path to a JSON file with camera calibration parameters (width, height, distortion_model, D, K, R, P).",
    )
    parser.add_argument(
        "--calibration-interval",
        type=int,
        default=10,
        help="Interval (seconds) at which calibration data is re-published.",
    )

    args = parser.parse_args()

    # Setup logging using scaffolding
    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    logger.info("Starting camera2keelson")
    logger.info(f"Realm: {args.realm}")
    logger.info(f"Entity ID: {args.entity_id}")
    logger.info(f"Source ID: {args.source_id}")

    # Validate calibration file early (fail fast before opening Zenoh session)
    if args.calibration_file is not None:
        try:
            cal_data = json.loads(args.calibration_file.read_text(encoding="UTF-8"))
            validate(cal_data, CALIBRATION_SCHEMA)
        except json.JSONDecodeError:
            logger.exception("Calibration file is not valid JSON!")
            sys.exit(1)
        except ValidationError:
            logger.exception("Calibration file does not match expected schema!")
            sys.exit(1)
        args._calibration_data = cal_data
        logger.info("Calibration file validated successfully")

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
