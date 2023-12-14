import time
from typing import Tuple
from pathlib import Path

import yaml
import parse
from google.protobuf.message_factory import GetMessages
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.descriptor import Descriptor, FileDescriptor

from .core_pb2 import Envelope, TimestampedTopicEnvelopePair
from . import payloads

_PACKAGE_ROOT = Path(__file__).parent

# TOPIC HELPER FUNCTIONS
KEELSON_BASE_TOPIC_FORMAT = "{realm}/{entity_id}/{interface_type}/{interface_id}"
KEELSON_PUB_SUB_TOPIC_FORMAT = KEELSON_BASE_TOPIC_FORMAT + "/{tag}/{source_id}"
KEELSON_REQ_REP_TOPIC_FORMAT = KEELSON_BASE_TOPIC_FORMAT + "/rpc/{procedure}"

PUB_SUB_TOPIC_PARSER = parse.compile(KEELSON_PUB_SUB_TOPIC_FORMAT)


def construct_pub_sub_topic(
    realm: str,
    entity_id: str,
    interface_type: str,
    interface_id: str,
    tag: str,
    source_id: str,
):
    return KEELSON_PUB_SUB_TOPIC_FORMAT.format(
        realm=realm,
        entity_id=entity_id,
        interface_type=interface_type,
        interface_id=interface_id,
        tag=tag,
        source_id=source_id,
    )


def construct_req_rep_topic(
    realm: str, entity_id: str, interface_type: str, interface_id: str, procedure: str
):
    return KEELSON_REQ_REP_TOPIC_FORMAT.format(
        realm=realm,
        entity_id=entity_id,
        interface_type=interface_type,
        interface_id=interface_id,
        procedure=procedure,
    )


def parse_pub_sub_topic(topic: str):
    if not (res := PUB_SUB_TOPIC_PARSER.parse(topic)):
        raise ValueError(
            f"Provided topic {topic} did not have the expected format {KEELSON_PUB_SUB_TOPIC_FORMAT}"
        )

    return res.named


def get_tag_from_pub_sub_topic(topic: str) -> str:
    return parse_pub_sub_topic(topic)["tag"]


## ENVELOPE HELPER FUNCTIONS
def enclose(payload: bytes, enclosed_at: int = None) -> bytes:
    env: Envelope = Envelope()
    env.enclosed_at.FromNanoseconds(enclosed_at or time.time_ns())
    env.payload = payload

    return env.SerializeToString()


def uncover(message: bytes) -> Tuple[int, Tuple[int, bytes]]:
    env = Envelope.FromString(message)
    return time.time_ns(), env.enclosed_at.ToNanoseconds(), env.payload


## PROTOBUF PAYLOADS HELPER FUNCTIONS
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


def get_protobuf_file_descriptor_set_from_type_name(type_name: str) -> Descriptor:
    return _assemble_file_descriptor_set(_PROTOBUF_INSTANCES[type_name].DESCRIPTOR)


def decode_protobuf_payload_from_type_name(payload: bytes, type_name: str):
    return _PROTOBUF_INSTANCES[type_name].FromString(payload)


## TAGS HELPER FUNCTIONS
with (_PACKAGE_ROOT / "tags.yaml").open() as fh:
    _TAGS = yaml.safe_load(fh)


def is_tag_well_known(tag: str) -> bool:
    return tag in _TAGS


def get_tag_encoding(tag: str) -> str:
    return _TAGS[tag]["encoding"]


def get_tag_description(tag: str) -> str:
    return _TAGS[tag]["description"]
