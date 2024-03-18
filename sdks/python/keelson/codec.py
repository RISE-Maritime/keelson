import sys
import json
import logging
import warnings
import argparse
from base64 import b64encode, b64decode

import parse
from google.protobuf.json_format import ParseDict, MessageToDict

from . import (
    enclose,
    uncover,
    is_subject_well_known,
    get_subject_from_pub_sub_key,
    get_subject_schema,
    decode_protobuf_payload_from_type_name,
    get_protobuf_message_class_from_type_name,
)

logger = logging.getLogger(__file__)


def enclose_from_text(key: str, value: str) -> bytes:
    return enclose(value.encode())


def enclose_from_base64(key: str, value: str) -> bytes:
    return enclose(b64decode(value.encode()))


def enclose_from_json(key: str, value: str) -> bytes:
    subject = get_subject_from_pub_sub_key(key)

    if not is_subject_well_known(subject):
        raise RuntimeError(f"Tag ({subject}) is not well-known!")

    type_name = get_subject_schema(subject)
    message = get_protobuf_message_class_from_type_name(type_name)()
    pb2js = json.loads(value)
    payload = ParseDict(pb2js, message)
    return enclose(payload.SerializeToString())


def uncover_to_text(key: str, value: bytes) -> str:
    received_at, enclosed_at, payload = uncover(value)
    return payload.decode()


def uncover_to_base64(key: str, value: bytes) -> str:
    received_at, enclosed_at, payload = uncover(value)
    return b64encode(payload).decode()


def uncover_to_json(key: str, value: bytes) -> str:
    key = str(key)
    received_at, enclosed_at, payload = uncover(value)

    subject = get_subject_from_pub_sub_key(key)

    if not is_subject_well_known(subject):
        raise RuntimeError(f"Tag ({subject}) is not well-known!")

    type_name = get_subject_schema(subject)
    message = decode_protobuf_payload_from_type_name(payload, type_name)
    return json.dumps(
        MessageToDict(
            message,
            including_default_value_fields=True,
            preserving_proto_field_name=True,
            use_integers_for_enums=True,
        )
    )
