import time
from typing import Tuple
from pathlib import Path

import yaml
from google.protobuf.descriptor import Descriptor
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message_factory import MessageFactory, GetMessages

from .envelope_pb2 import Envelope
from . import payloads

PACKAGE_ROOT = Path(__file__).parent

# Load tags.yaml
with (PACKAGE_ROOT / "tags.yaml").open() as fh:
    TAGS = yaml.safe_load(fh)

# Load generated file_descriptor_set and generate message classes
with (PACKAGE_ROOT / "payloads" / "protobuf_file_descriptor_set.bin").open("rb") as fh:
    PROTOBUF_FILE_DESCRIPTOR_SET = FileDescriptorSet.FromString(fh.read())

PROTOBUF_INSTANCES = GetMessages(PROTOBUF_FILE_DESCRIPTOR_SET.file)


def enclose(payload: bytes) -> bytes:
    env: Envelope = Envelope()
    env.enclosed_at.FromNanoseconds(time.time_ns())
    env.payload = payload

    return env.SerializeToString()


def uncover(message: bytes) -> Tuple[int, Tuple[int, bytes]]:
    env = Envelope.FromString(message)
    return time.time_ns(), env.enclosed_at.ToNanoseconds(), env.payload


def get_tag_specification(tag: str) -> dict:
    return TAGS[tag]


def get_protobuf_descriptor_from_type_name(type_name: str) -> Descriptor:
    return PROTOBUF_INSTANCES[type_name].DESCRIPTOR


def decode_protobuf_payload_from_type_name(payload: bytes, type_name: str):
    return PROTOBUF_INSTANCES[type_name].FromString(payload)


__all__ = [
    "enclose",
    "uncover",
    "get_tag_specification",
    "get_protobuf_descriptor_from_type_name",
    "decode_protobuf_payload_from_type_name",
    "payloads",
]
