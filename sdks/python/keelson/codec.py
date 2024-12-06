import json
import time
import logging
from base64 import b64encode, b64decode

from google.protobuf.json_format import ParseDict, MessageToDict

from . import (
    enclose,
    uncover,
    is_subject_well_known,
    get_subject_from_pubsub_key,
    get_subject_schema,
    decode_protobuf_payload_from_type_name,
    get_protobuf_message_class_from_type_name,
)

from .payloads.Primitives_pb2 import TimestampedBytes

logger = logging.getLogger(__file__)


def enclose_from_text(key: str, value: str) -> bytes:
    subject = get_subject_from_pubsub_key(key)

    if subject != "raw":
        raise ValueError(
            f"keelson-enclose-from-text can only be used together with a 'raw' subject! You tried to use it with '{subject}'"
        )

    payload = TimestampedBytes()
    payload.timestamp.FromNanoseconds(time.time_ns())
    payload.value = value.encode()

    return enclose(payload.SerializeToString())


def enclose_from_base64(key: str, value: str) -> bytes:
    subject = get_subject_from_pubsub_key(key)

    if subject != "raw":
        raise ValueError(
            f"keelson-enclose-from-base64 can only be used together with a 'raw' subject! You tried to use it with '{subject}'"
        )

    payload = TimestampedBytes()
    payload.timestamp.FromNanoseconds(time.time_ns())
    payload.value = b64decode(value.encode())

    return enclose(payload.SerializeToString())


def enclose_from_json(key: str, value: str) -> bytes:
    subject = get_subject_from_pubsub_key(key)

    if not is_subject_well_known(subject):
        raise RuntimeError(f"Tag ({subject}) is not well-known!")

    type_name = get_subject_schema(subject)
    message = get_protobuf_message_class_from_type_name(type_name)()
    pb2js = json.loads(value)
    payload = ParseDict(pb2js, message)
    return enclose(payload.SerializeToString())


def uncover_to_text(key: str, value: bytes) -> str:
    subject = get_subject_from_pubsub_key(str(key))

    if subject != "raw":
        raise ValueError(
            f"keelson-uncover-to-text can only be used together with a 'raw' subject! You tried to use it with '{subject}'"
        )

    received_at, enclosed_at, payload = uncover(value)
    parsed = TimestampedBytes.FromString(payload)
    return parsed.value.decode()


def uncover_to_base64(key: str, value: bytes) -> str:
    subject = get_subject_from_pubsub_key(str(key))

    if subject != "raw":
        raise ValueError(
            f"keelson-uncover-to-base64 can only be used together with a 'raw' subject! You tried to use it with '{subject}'"
        )

    received_at, enclosed_at, payload = uncover(value)
    parsed = TimestampedBytes.FromString(payload)
    return b64encode(parsed.value).decode()


def uncover_to_json(key: str, value: bytes) -> str:
    subject = get_subject_from_pubsub_key(str(key))

    if not is_subject_well_known(subject):
        raise RuntimeError(f"Tag ({subject}) is not well-known!")

    type_name = get_subject_schema(subject)
    received_at, enclosed_at, payload = uncover(value)
    message = decode_protobuf_payload_from_type_name(payload, type_name)
    return json.dumps(
        MessageToDict(
            message,
            always_print_fields_with_no_presence=True,
            preserving_proto_field_name=True,
            use_integers_for_enums=True,
        )
    )
