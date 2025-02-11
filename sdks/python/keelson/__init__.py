import time
from typing import Tuple
from pathlib import Path
import os
from enum import Enum

import yaml
import parse
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessages
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.descriptor import Descriptor, FileDescriptor

# from Envelope_pb2 import Envelope
from .Envelope_pb2 import Envelope
from . import payloads

_PACKAGE_ROOT = Path(__file__).parent

# KEY HELPER FUNCTIONS
KEELSON_BASE_KEY_FORMAT = "{realm}/v0/{entity_id}"
KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
KEELSON_REQ_REP_KEY_FORMAT = (
    KEELSON_BASE_KEY_FORMAT + "/rpc/{procedure}/{subject_in}/{subject_out}/{source_id}"
)

PUB_SUB_KEY_PARSER = parse.compile(KEELSON_PUB_SUB_KEY_FORMAT)
REQ_REP_KEY_PARSER = parse.compile(KEELSON_REQ_REP_KEY_FORMAT)


def construct_pubsub_key(
    realm: str,
    entity_id: str,
    subject: str,
    source_id: str,
):
    """
    Construct a key expression for a publish subscribe interaction (Observable).

    Args:
        realm (str): The realm of the entity.
        entity_id (str): The entity id.
        subject (str): The subject of the interaction.
        source_id (str): The source id of the entity.

    Returns:
        key_expression (str):
            The constructed key.


    ## Well-known subjects

    ### raw
    - raw [keelson.primitives.TimestampedBytes](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedBytes.proto)
    - raw_bytes [keelson.primitives.TimestampedBytes](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedBytes.proto)
    - raw_string [keelson.primitives.TimestampedString](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedString.proto)
    - raw_json [keelson.primitives.TimestampedString](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedString.proto)

    ### log
    - log [foxglove.Log](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/Log.proto)

    ### primitive
    - percentage [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - degrees [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - rpm [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - meters [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - kilometers [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - meters_per_second [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - kilometers_per_hour [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - nautical_miles [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - knots [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)

    ### network
    - network_ping [keelson.compound.NetworkPing]
    - network_result [keelson.compound.NetworkResult]

    ### navigation
    - target [keelson.compound.Target]
    - target_description [keelson.compound.TargetDescription]

    ### config
    - config_perception_sensor [keelson.experimental.ConfigurationSensorPerception]

    ### image
    - raw_image [foxglove.RawImage]
    - compressed_image [foxglove.CompressedImage]

    ### lidar
    - laser_scan [foxglove.LaserScan]

    ### radar
    - radar_spoke [keelson.compound.RadarSpoke]
    - radar_sweep [keelson.compound.RadarSweep]

    ### point cloud
    - point_cloud [foxglove.PointCloud]
    - point_cloud_simplified [keelson.experimental.PointCloudSimplified]

    ### imu
    - imu_reading [keelson.compound.ImuReading]

    ### control
    - sail_control_state [keelson.compound.SailControlState]
    - sail_state [keelson.compound.SailState]
    - thrust_command [keelson.compound.CommandThruster]
    - camera_command_xy [keelson.compound.CommandCameraXY]

    ### simulation
    - simulation_state [keelson.compound.SimulationState]
    - simulation_ship [keelson.compound.SimulationShip]

    ### nmea
    - nmea_string [keelson.primitives.TimestampedString]
    - nmea_gngns [keelson.compound.GNGNS]

    ### mavlink
    - flight_controller_telemetry_vfrhud [keelson.experimental.VFRHUD]
    - flight_controller_telemetry_rawimu [keelson.experimental.RawIMU]
    - flight_controller_telemetry_ahrs [keelson.experimental.AHRSs]
    - flight_controller_telemetry_vibration [keelson.experimental.Vibration]
    - flight_controller_telemetry_battery [keelson.experimental.BatteryStatus]
    - lever_position_pct [keelson.primitives.TimestampedFloat]
    - propeller_rate_rpm [keelson.primitives.TimestampedFloat]
    - propeller_pitch_rpm [keelson.primitives.TimestampedFloat]

    """

    return KEELSON_PUB_SUB_KEY_FORMAT.format(
        realm=realm,
        entity_id=entity_id,
        subject=subject,
        source_id=source_id,
    )


def construct_rpc_key(
    realm: str,
    entity_id: str,
    procedure: str,
    subject_in: str,
    subject_out: str,
    source_id: str,
):
    """
    Construct a key expression for a request reply interaction (Queryable/RPC).

    Args:
        realm (str): The realm of the entity.
        entity_id (str): The entity id.
        procedure (str): The procedure being called for identifying the specific service
        subject_in (str): Well known subject as input payload or none if not applicable
        subject_out (str): Well known subject as output payload or none if not applicable
        source_id (str): The source id of the entity being targeted

    Returns:
        key_expression (str):
            The constructed key.


    ## Well-known subjects

    ### raw
    - raw [keelson.primitives.TimestampedBytes](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedBytes.proto)
    - raw_bytes [keelson.primitives.TimestampedBytes](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedBytes.proto)
    - raw_string [keelson.primitives.TimestampedString](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedString.proto)
    - raw_json [keelson.primitives.TimestampedString](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedString.proto)

    ### log
    - log [foxglove.Log](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/Log.proto)

    ### primitive
    - percentage [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - degrees [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - rpm [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - meters [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - kilometers [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - meters_per_second [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - kilometers_per_hour [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - nautical_miles [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
    - knots [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)

    ### network
    - network_ping [keelson.compound.NetworkPing]
    - network_result [keelson.compound.NetworkResult]

    ### navigation
    - target [keelson.compound.Target]
    - target_description [keelson.compound.TargetDescription]

    ### config
    - config_perception_sensor [keelson.experimental.ConfigurationSensorPerception]

    ### image
    - raw_image [foxglove.RawImage]
    - compressed_image [foxglove.CompressedImage]

    ### lidar
    - laser_scan [foxglove.LaserScan]

    ### radar
    - radar_spoke [keelson.compound.RadarSpoke]
    - radar_sweep [keelson.compound.RadarSweep]

    ### point cloud
    - point_cloud [foxglove.PointCloud]
    - point_cloud_simplified [keelson.experimental.PointCloudSimplified]

    ### imu
    - imu_reading [keelson.compound.ImuReading]

    ### control
    - sail_control_state [keelson.compound.SailControlState]
    - sail_state [keelson.compound.SailState]
    - thrust_command [keelson.compound.CommandThruster]
    - camera_command_xy [keelson.compound.CommandCameraXY]

    ### simulation
    - simulation_state [keelson.compound.SimulationState]
    - simulation_ship [keelson.compound.SimulationShip]

    ### nmea
    - nmea_string [keelson.primitives.TimestampedString]
    - nmea_gngns [keelson.compound.GNGNS]

    ### mavlink
    - flight_controller_telemetry_vfrhud [keelson.experimental.VFRHUD]
    - flight_controller_telemetry_rawimu [keelson.experimental.RawIMU]
    - flight_controller_telemetry_ahrs [keelson.experimental.AHRSs]
    - flight_controller_telemetry_vibration [keelson.experimental.Vibration]
    - flight_controller_telemetry_battery [keelson.experimental.BatteryStatus]
    - lever_position_pct [keelson.primitives.TimestampedFloat]
    - propeller_rate_rpm [keelson.primitives.TimestampedFloat]
    - propeller_pitch_rpm [keelson.primitives.TimestampedFloat]


    """
    return KEELSON_REQ_REP_KEY_FORMAT.format(
        realm=realm,
        entity_id=entity_id,
        procedure=procedure,
        subject_in=subject_in,
        subject_out=subject_out,
        source_id=source_id,
    )


def parse_pubsub_key(key: str):
    """
    Parse a key expression for a publish subscribe interaction (Observable).

    Args:
        key (str): The key expression to parse.

    Returns:
        Dict (dict):
            The parsed key expression.

        Dictionary keys:
            realm (str):
                The realm of the entity.
            entity_id (str):
                The entity id.
            subject (str):
                The subject of the interaction.
            source_id (str):
                The source id of the entity
    """
    if not (res := PUB_SUB_KEY_PARSER.parse(key)):
        raise ValueError(
            f"Provided key {key} did not have the expected format {KEELSON_PUB_SUB_KEY_FORMAT}"
        )

    return res.named


def parse_rpc_key(key: str):
    """
    Parse a key expression for a request reply interaction (Queryable).

    Args:
        key (str): The key expression to parse.

    Returns:
        Dict (dict):
            The parsed key expression.

        Dictionary keys:
            realm (str):
                The realm of the entity.
            entity_id (str):
                The entity id.
            procedure (str):
                The procedure being called.
            target_id (str):
                The target id of the entity being called.

    """

    if not (res := REQ_REP_KEY_PARSER.parse(key)):
        raise ValueError(
            f"Provided key {key} did not have the expected format {KEELSON_REQ_REP_KEY_FORMAT}"
        )

    return res.named


def get_subject_from_pubsub_key(key: str) -> str:
    """
    Get the subject from a key expression for a publish subscribe interaction (Observable).
    """
    return parse_pubsub_key(key)["subject"]


def get_subjects_from_rpc_key(key: str) -> str:
    """
    Get the subjects from a key expression for a request reply interaction (Queryable).
    """
    return parse_rpc_key(key)["subject_in"], parse_rpc_key(key)["subject_out"]


# ENVELOPE HELPER FUNCTIONS
def enclose(payload: bytes, enclosed_at: int = None) -> bytes:
    """
    Enclose a payload in an envelope.

    Args:
        payload (bytes): The payload to enclose.
        enclosed_at (int): The time at which the envelope was enclosed.
        source_timestamp (int): The source timestamp of the payload.

    Returns:
        envelope (bytes):
            The enclosed envelope.
    """
    env: Envelope = Envelope()
    env.enclosed_at.FromNanoseconds(enclosed_at or time.time_ns())
    env.payload = payload
    return env.SerializeToString()


def uncover(message) -> object:
    """
    Uncover Keelson message that is an envelope

    Args:
        message (bytes): The envelope to uncover.

    Returns:
        Object ( int, int, int, bytes):
            enclosed_at, received_at,  payload

    Example:

    ```
    received_at, enclosed_at, payload = uncover(message)
    ```

    """
    env = Envelope.FromString(message)

    enclose_at = env.enclosed_at.ToNanoseconds()
    received_at = time.time_ns()
    payload = env.payload

    return enclose_at, received_at, payload


# PROTOBUF PAYLOADS HELPER FUNCTIONS
with (_PACKAGE_ROOT / "payloads" / "protobuf_file_descriptor_set.bin").open("rb") as fh:
    _PROTOBUF_FILE_DESCRIPTOR_SET = FileDescriptorSet.FromString(fh.read())

_PROTOBUF_INSTANCES = GetMessages(_PROTOBUF_FILE_DESCRIPTOR_SET.file)


def _assemble_file_descriptor_set(descriptor: Descriptor) -> FileDescriptorSet:
    file_descriptor_set = FileDescriptorSet()
    seen_deps = set()

    def _add_file_descriptor(file_descriptor: FileDescriptor):
        for dep in file_descriptor.dependencies:
            if dep.name not in seen_deps:
                seen_deps.add(dep.name)
                _add_file_descriptor(dep)
        file_descriptor.CopyToProto(file_descriptor_set.file.add())

    _add_file_descriptor(descriptor.file)
    return file_descriptor_set


def get_protobuf_message_class_from_type_name(type_name: str) -> Message:
    return _PROTOBUF_INSTANCES[type_name]


def decode_protobuf_payload_from_type_name(payload: bytes, type_name: str):
    return get_protobuf_message_class_from_type_name(type_name).FromString(payload)


def get_protobuf_file_descriptor_set_from_type_name(type_name: str) -> Descriptor:
    return _assemble_file_descriptor_set(
        get_protobuf_message_class_from_type_name(type_name).DESCRIPTOR
    )


# TAGS HELPER FUNCTIONS
with (_PACKAGE_ROOT / "subjects.yaml").open() as fh:
    _SUBJECTS = yaml.safe_load(fh)


def is_subject_well_known(subject: str) -> bool:
    return subject in _SUBJECTS


def get_subject_schema(subject: str) -> str:
    return _SUBJECTS[subject]["schema"]
