import time
import logging
from typing import Tuple
from pathlib import Path

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
KEELSON_BASE_KEY_FORMAT = "{base_path}/@v0/{entity_id}"
KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
KEELSON_REQ_REP_KEY_FORMAT = (
    KEELSON_BASE_KEY_FORMAT + "/@rpc/{procedure}/{responder_id}"
)

PUB_SUB_KEY_PARSER = parse.compile(KEELSON_PUB_SUB_KEY_FORMAT)
REQ_REP_KEY_PARSER = parse.compile(KEELSON_REQ_REP_KEY_FORMAT)

logger = logging.getLogger("keelson")


def construct_pubsub_key(
    base_path: str,
    entity_id: str,
    subject: str,
    source_id: str,
    target_id: str = None,
):
    """
    Construct a key expression for a publish subscribe interaction (Observable).

    Args:
        realm (str): The realm of the entity.
        entity_id (str): The entity id.
        subject (str): The subject of the interaction.
        source_id (str): The source id of the entity.
        target_id (str) (Optional): The id of the (optionally) referred entity

    Returns:
        key_expression (str):
            The constructed key.

    """

    if not is_subject_well_known(subject):
        logger.warning("Subject: %s is NOT well-known!", subject)

    key = KEELSON_PUB_SUB_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        subject=subject,
        source_id=source_id,
    )

    return key if not target_id else f"{key}/@target/{target_id}"


def construct_rpc_key(
    base_path: str,
    entity_id: str,
    procedure: str,
    responder_id: str,
):
    """
    Construct a key expression for a request reply interaction (Queryable/RPC).

    Args:
        realm (str): The realm of the entity.
        entity_id (str): The entity id.
        procedure (str): The procedure being called for identifying the specific service
        responder_id (str): The responder id of the entity being targeted

    Returns:
        key_expression (str):
            The constructed key.


        ## Well-known subjects

    [GITHUB DOC SUBJECTS](https://github.com/RISE-Maritime/keelson/blob/main/messages/subjects.yaml)

    """
    return KEELSON_REQ_REP_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        procedure=procedure,
        responder_id=responder_id,
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


def uncover(message) -> Tuple[int, int, bytes]:
    """
    Uncover Keelson message that is an envelope

    Args:
        message (bytes): The envelope to uncover.

    Returns:
        Object ( int, int, bytes):
            received_at, enclosed_at, payload

    Example:

    ```
    received_at, enclosed_at, payload = uncover(message)
    ```

    """
    env = Envelope.FromString(message)

    return time.time_ns(), env.enclosed_at.ToNanoseconds(), env.payload


###### Payload handling #####

_PROTO_TYPES = {}
_SUBJECTS = {}


def add_well_known_subjects_and_proto_definitions(
    path_to_subjects_yaml: Path, path_to_proto_file_descriptor_set: Path = None
):
    with path_to_subjects_yaml.open() as fh:
        _SUBJECTS.update(yaml.safe_load(fh))

    if path_to_proto_file_descriptor_set is not None:
        with path_to_proto_file_descriptor_set.open("rb") as fh:
            _PROTO_TYPES.update(
                GetMessages(FileDescriptorSet.FromString(fh.read()).file)
            )


# Add the bundled well-known subjects and types
add_well_known_subjects_and_proto_definitions(
    _PACKAGE_ROOT / "subjects.yaml",
    _PACKAGE_ROOT / "payloads" / "protobuf_file_descriptor_set.bin",
)


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
    return _PROTO_TYPES[type_name]


def decode_protobuf_payload_from_type_name(payload: bytes, type_name: str):
    return get_protobuf_message_class_from_type_name(type_name).FromString(payload)


def get_protobuf_file_descriptor_set_from_type_name(type_name: str) -> Descriptor:
    return _assemble_file_descriptor_set(
        get_protobuf_message_class_from_type_name(type_name).DESCRIPTOR
    )


# SUBJECTS HELPER FUNCTIONS
def is_subject_well_known(subject: str) -> bool:
    return subject in _SUBJECTS


def get_subject_schema(subject: str) -> str:
    return _SUBJECTS[subject]
