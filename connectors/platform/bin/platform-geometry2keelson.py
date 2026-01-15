#!/usr/bin/env python3

"""
Command line utility tool for outputting geometrical information about a platform on a given interval
"""

# pylint: disable=duplicate-code
# pylint: disable=invalid-name

import sys
import time
import json
import logging
import argparse
from pathlib import Path

import zenoh
from scipy.spatial.transform import Rotation
from jsonschema import validate, ValidationError
from keelson import construct_pubsub_key, enclose
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.payloads.foxglove.FrameTransform_pb2 import FrameTransform
from keelson_connectors_common import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
)

logger = logging.getLogger("platform-geometry")

JSON_SCHEMA = """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "title": "Platform Geometry",
  "description": "Geometrical information about a specific platform.",
  "properties": {
    "vessel_name": {
      "type": "string"
    },
    "length_over_all_m": {
      "type": "number"
    },
    "breadth_over_all_m": {
      "type": "number"
    },
    "frame_transforms": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "parent_frame_id",
          "child_frame_id",
          "translation",
          "rotation"
        ],
        "properties": {
          "parent_frame_id": {
            "type": "string"
          },
          "child_frame_id": {
            "type": "string"
          },
          "translation": {
            "type": "object",
            "title": "Translation",
            "description": "A translation of the child frame in relation to the parent frame expressed in the parent frame of reference.",
            "properties": {
              "x": { "type": "number" },
              "y": { "type": "number" },
              "z": { "type": "number" }
            },
            "required": ["x", "y", "z"],
            "additionalProperties": false
          },
          "rotation": {
            "type": "object",
            "title": "Rotation",
            "description": "A rotation of the child frame in relation to the parent frame expressed in the parent frame of reference given as Euler angles according to the YPR convention",
            "properties": {
              "roll": { "type": "number" },
              "pitch": { "type": "number" },
              "yaw": { "type": "number" }
            },
            "required": ["roll", "pitch", "yaw"],
            "additionalProperties": false
          }
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
}
"""


def run(session: zenoh.Session, args: argparse.Namespace, config: dict):

    # Set up keys
    key_loa = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "length_over_all_m",
        args.source_id,
    )

    key_boa = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "breadth_over_all_m",
        args.source_id,
    )

    key_frame_transform = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "frame_transform",
        args.source_id,
    )

    # Lets start pushing messages
    while True:

        # Lets give all messages in this iteration the same timestamp
        timestamp = time.time_ns()

        if loa := config.get("length_over_all_m"):
            payload = TimestampedFloat()
            payload.timestamp.FromNanoseconds(timestamp)
            payload.value = loa

            logger.debug("Putting to %s", key_loa)
            session.put(
                key_loa, enclose(payload.SerializeToString(), enclosed_at=timestamp)
            )

        if boa := config.get("breadth_over_all_m"):
            payload = TimestampedFloat()
            payload.timestamp.FromNanoseconds(timestamp)
            payload.value = boa

            logger.debug("Putting to %s", key_boa)
            session.put(
                key_boa, enclose(payload.SerializeToString(), enclosed_at=timestamp)
            )

        for transform in config.get("frame_transforms", []):
            payload = FrameTransform()
            payload.timestamp.FromNanoseconds(timestamp)

            payload.parent_frame_id = transform["parent_frame_id"]
            payload.child_frame_id = transform["child_frame_id"]

            payload.translation.x = transform["translation"]["x"]
            payload.translation.y = transform["translation"]["y"]
            payload.translation.z = transform["translation"]["z"]

            quat = Rotation.from_euler(
                "zyx",
                [
                    transform["rotation"]["yaw"],
                    transform["rotation"]["pitch"],
                    transform["rotation"]["roll"],
                ],
                degrees=True,
            ).as_quat()

            payload.rotation.x = quat[0]
            payload.rotation.y = quat[1]
            payload.rotation.z = quat[2]
            payload.rotation.w = quat[3]

            logger.debug("Putting to %s", key_frame_transform)
            session.put(
                key_frame_transform,
                enclose(payload.SerializeToString(), enclosed_at=timestamp),
            )

        time.sleep(args.interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="platform-geomtry",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Command line utility tool for outputting geometrical information about a platform on a given interval",
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, required=True)

    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="A path to a JSON-encoded configuration file for this platform.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Interval (second) at whic the information will be put to zenoh.",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

    # Load and validate json config file
    try:
        config = json.loads(args.config.read_text(encoding="UTF-8"))
        validate(config, json.loads(JSON_SCHEMA))
    except json.JSONDecodeError:
        logger.exception("The provided config file is not valid JSON!")
        sys.exit(1)
    except ValidationError:
        logger.exception(
            "The provided config file does not validate against the JSON schema!"
        )
        sys.exit(1)

    # Construct session
    logger.info("Opening Zenoh session...")
    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    with zenoh.open(zconf) as session:
        # Dispatch to correct function
        try:
            run(session, args, config)
        except KeyboardInterrupt:
            logger.info("Closing down on user request!")
            sys.exit(0)
