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
import threading
from pathlib import Path

import zenoh
from squaternion import Quaternion
from jsonschema import validate, ValidationError
from keelson import construct_pubsub_key, construct_rpc_key, enclose
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedInt, TimestampedString
from keelson.payloads.foxglove.FrameTransform_pb2 import FrameTransform
from keelson.payloads.foxglove.CameraCalibration_pb2 import CameraCalibration
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    GracefulShutdown,
    make_configurable,
)

logger = logging.getLogger("platform-geometry")

_SCHEMA_PATH = Path(__file__).parent.parent / "config-schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="UTF-8"))

# Module-level mutable config protected by a lock (allows set_config from RPC callbacks)
_config: dict = {}
_config_lock = threading.Lock()


def get_config() -> dict:
    with _config_lock:
        return dict(_config)


def set_config(new_config: dict) -> None:
    validate(new_config, _SCHEMA)
    with _config_lock:
        _config.clear()
        _config.update(new_config)
    logger.info("Configuration updated")


def run(session: zenoh.Session, args: argparse.Namespace):

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

    key_mmsi = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "mmsi_number",
        args.source_id,
    )

    key_call_sign = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "call_sign",
        args.source_id,
    )

    key_imo = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "imo_number",
        args.source_id,
    )

    key_camera_calibration = construct_pubsub_key(
        args.realm,
        args.entity_id,
        "camera_calibration",
        args.source_id,
    )

    with GracefulShutdown() as shutdown:
        while not shutdown.is_requested():

            # Take a snapshot of the current config for this iteration
            with _config_lock:
                config = dict(_config)

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

            if mmsi := config.get("mmsi_number"):
                payload = TimestampedInt()
                payload.timestamp.FromNanoseconds(timestamp)
                payload.value = mmsi

                logger.debug("Putting to %s", key_mmsi)
                session.put(
                    key_mmsi, enclose(payload.SerializeToString(), enclosed_at=timestamp)
                )

            if call_sign := config.get("call_sign"):
                payload = TimestampedString()
                payload.timestamp.FromNanoseconds(timestamp)
                payload.value = call_sign

                logger.debug("Putting to %s", key_call_sign)
                session.put(
                    key_call_sign,
                    enclose(payload.SerializeToString(), enclosed_at=timestamp),
                )

            if (imo := config.get("imo_number")) is not None:
                payload = TimestampedInt()
                payload.timestamp.FromNanoseconds(timestamp)
                payload.value = imo

                logger.debug("Putting to %s", key_imo)
                session.put(
                    key_imo, enclose(payload.SerializeToString(), enclosed_at=timestamp)
                )

            for transform in config.get("frame_transforms", []):
                payload = FrameTransform()
                payload.timestamp.FromNanoseconds(timestamp)

                payload.parent_frame_id = transform["parent_frame_id"]
                payload.child_frame_id = transform["child_frame_id"]

                payload.translation.x = transform["translation_m"]["x"]
                payload.translation.y = transform["translation_m"]["y"]
                payload.translation.z = transform["translation_m"]["z"]

                q = Quaternion.from_euler(
                    transform["rotation_deg"]["roll"],
                    transform["rotation_deg"]["pitch"],
                    transform["rotation_deg"]["yaw"],
                    degrees=True,
                )

                payload.rotation.x = q.x
                payload.rotation.y = q.y
                payload.rotation.z = q.z
                payload.rotation.w = q.w

                logger.debug("Putting to %s", key_frame_transform)
                session.put(
                    key_frame_transform,
                    enclose(payload.SerializeToString(), enclosed_at=timestamp),
                )

            for cal in config.get("camera_calibrations", []):
                payload = CameraCalibration()
                payload.timestamp.FromNanoseconds(timestamp)
                payload.frame_id = cal["frame_id"]
                payload.width = cal["width"]
                payload.height = cal["height"]

                if "distortion_model" in cal:
                    payload.distortion_model = cal["distortion_model"]
                payload.D[:] = cal.get("D", [])
                payload.K[:] = cal.get("K", [])
                payload.R[:] = cal.get("R", [])
                payload.P[:] = cal.get("P", [])

                logger.debug("Putting to %s", key_camera_calibration)
                session.put(
                    key_camera_calibration,
                    enclose(payload.SerializeToString(), enclosed_at=timestamp),
                )

            time.sleep(args.interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="platform-geometry",
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
        help="Interval (second) at which the information will be put to zenoh.",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

    # Load and validate json config file
    try:
        initial_config = json.loads(args.config.read_text(encoding="UTF-8"))
        validate(initial_config, _SCHEMA)
        _config.update(initial_config)
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
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            make_configurable(
                session=session,
                base_path=args.realm,
                entity_id=args.entity_id,
                responder_id=args.source_id,
                get_config_cb=get_config,
                set_config_cb=set_config,
            )

            # Declare get_data_streams queryable
            _key_get_ds = construct_rpc_key(
                args.realm, args.entity_id, "get_data_streams", args.source_id
            )

            def _get_data_streams(query: zenoh.Query):
                payload = TimestampedString()
                payload.timestamp.FromNanoseconds(time.time_ns())
                payload.value = json.dumps(get_config().get("data_streams", []))
                query.reply(_key_get_ds, enclose(payload.SerializeToString()))

            session.declare_queryable(_key_get_ds, _get_data_streams, complete=True)

            # Declare get_queryables queryable
            _key_get_q = construct_rpc_key(
                args.realm, args.entity_id, "get_queryables", args.source_id
            )

            def _get_queryables(query: zenoh.Query):
                payload = TimestampedString()
                payload.timestamp.FromNanoseconds(time.time_ns())
                payload.value = json.dumps(get_config().get("queryables", []))
                query.reply(_key_get_q, enclose(payload.SerializeToString()))

            session.declare_queryable(_key_get_q, _get_queryables, complete=True)

            # Log all active pub/sub keys and queryables
            _key_loa = construct_pubsub_key(args.realm, args.entity_id, "length_over_all_m", args.source_id)
            _key_boa = construct_pubsub_key(args.realm, args.entity_id, "breadth_over_all_m", args.source_id)
            _key_ft = construct_pubsub_key(args.realm, args.entity_id, "frame_transform", args.source_id)
            _key_mmsi = construct_pubsub_key(args.realm, args.entity_id, "mmsi_number", args.source_id)
            _key_cs = construct_pubsub_key(args.realm, args.entity_id, "call_sign", args.source_id)
            _key_imo = construct_pubsub_key(args.realm, args.entity_id, "imo_number", args.source_id)
            _key_cal = construct_pubsub_key(args.realm, args.entity_id, "camera_calibration", args.source_id)
            _key_config = construct_pubsub_key(args.realm, args.entity_id, "configuration_json", args.source_id)
            _key_get_config = construct_rpc_key(args.realm, args.entity_id, "get_config", args.source_id)
            _key_set_config = construct_rpc_key(args.realm, args.entity_id, "set_config", args.source_id)
            logger.info("Publishing on:")
            logger.info("  [pub] %s", _key_loa)
            logger.info("  [pub] %s", _key_boa)
            logger.info("  [pub] %s", _key_ft)
            _cfg = get_config()
            if _cfg.get("mmsi_number"):
                logger.info("  [pub] %s", _key_mmsi)
            if _cfg.get("call_sign"):
                logger.info("  [pub] %s", _key_cs)
            if _cfg.get("imo_number") is not None:
                logger.info("  [pub] %s", _key_imo)
            if _cfg.get("camera_calibrations"):
                logger.info("  [pub] %s  (%d calibration(s))", _key_cal, len(_cfg["camera_calibrations"]))
            logger.info("  [pub] %s", _key_config)
            logger.info("Queryables:")
            logger.info("  [rpc] %s", _key_get_config)
            logger.info("  [rpc] %s", _key_set_config)
            logger.info("  [rpc] %s", _key_get_ds)
            logger.info("  [rpc] %s", _key_get_q)
            _queryables = get_config().get("queryables", [])
            if _queryables:
                logger.info("Configured queryables (%d):", len(_queryables))
                for _q in _queryables:
                    _qdesc = f"  {_q['description']}" if _q.get("description") else ""
                    logger.info("  [rpc] %s%s", _q["key_expression"], _qdesc)
            _data_streams = get_config().get("data_streams", [])
            if _data_streams:
                logger.info("Expected data streams (%d):", len(_data_streams))
                for _ds in _data_streams:
                    _hz = _ds["expected_hz"]
                    _live = " [liveliness]" if _ds.get("liveliness") else ""
                    _desc = f"  {_ds['description']}" if _ds.get("description") else ""
                    logger.info("  [%.4g Hz]%s %s%s", _hz, _live, _ds["key_expression"], _desc)

            # Publish initial configuration so late-joining subscribers get the current state
            _payload = TimestampedString()
            _payload.timestamp.FromNanoseconds(time.time_ns())
            _payload.value = json.dumps(get_config())
            session.put(_key_config, enclose(_payload.SerializeToString()))

            run(session, args)
