import json
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
from .helpers import enclose_from_bytes, enclose_from_string

from .payloads.Primitives_pb2 import TimestampedBytes, TimestampedString

logger = logging.getLogger(__file__)

_BYTES = "keelson.TimestampedBytes"
_STRING = "keelson.TimestampedString"


def _schema_of(codec: str, key: str, accepted: tuple) -> str:
    """The subject's payload type, if `codec` can carry it.

    The text and base64 codecs move a bare value, so they fit any subject
    whose payload is a timestamped value of the matching kind (raw,
    raw_nmea0183, raw_lwe450, name, ...); the timestamp is set on enclose
    and dropped on uncover. Everything else goes through the json codec.
    """
    subject = get_subject_from_pubsub_key(str(key))

    if not is_subject_well_known(subject):
        raise ValueError(f"{codec}: subject '{subject}' is not well-known!")

    schema = get_subject_schema(subject)
    if schema not in accepted:
        raise ValueError(
            f"{codec} needs a subject whose payload is "
            f"{' or '.join(accepted)}, but '{subject}' is {schema}. "
            "Use the keelson json codec instead."
        )
    return schema


def enclose_from_text(key: str, value: str) -> bytes:
    schema = _schema_of("keelson-enclose-from-text", key, (_STRING, _BYTES))
    # zenoh-cli hands over each stdin line with its terminator; the connectors
    # publish lines without one, and so must this, or the same sentence
    # differs depending on who put it on the bus.
    value = value.rstrip("\r\n")
    if schema == _STRING:
        return enclose_from_string(value)
    return enclose_from_bytes(value.encode())


def enclose_from_base64(key: str, value: str) -> bytes:
    _schema_of("keelson-enclose-from-base64", key, (_BYTES,))
    return enclose_from_bytes(b64decode(value.encode()))


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
    schema = _schema_of("keelson-uncover-to-text", key, (_STRING, _BYTES))
    _, _, payload = uncover(value)
    if schema == _STRING:
        return TimestampedString.FromString(payload).value
    # Binary payloads (raw_nmea2000, raw_rtcm_v3) are rarely UTF-8; the
    # base64 codec is the lossless way to read those.
    return TimestampedBytes.FromString(payload).value.decode()


def uncover_to_base64(key: str, value: bytes) -> str:
    _schema_of("keelson-uncover-to-base64", key, (_BYTES,))
    _, _, payload = uncover(value)
    return b64encode(TimestampedBytes.FromString(payload).value).decode()


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
