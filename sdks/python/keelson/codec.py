import sys
import json
import logging
import warnings
import argparse
from base64 import b64encode, b64decode

import parse
from google.protobuf.json_format import ParseDict, MessageToDict

from . import enclose, uncover, is_tag_well_known, get_tag_encoding, get_tag_from_pub_sub_topic, get_tag_description, decode_protobuf_payload_from_type_name

logger = logging.getLogger(__file__)

def enclose_from_text(key: str, value: str) -> bytes:
    return enclose(value.encode())

def enclose_from_base64(key: str, value: str) -> bytes:
    return enclose(b64decode(value.encode()))

def enclose_from_json(key: str, value: str) -> bytes:
    tag = get_tag_from_pub_sub_topic(key)

    if not is_tag_well_known(tag):
        warnings.warn(f"Unknown tag {tag}")
        return enclose(value.encode())
    
    tag_encoding = get_tag_encoding(tag)

    if tag_encoding == "json":
        return enclose(value.encode())
    elif tag_encoding == "protobuf":
        type_name = get_tag_description(tag)
        message = get_protobuf_message_class_from_type_name(type_name)()
        pb2js = json.loads(value)
        payload = ParseDict(pb2js, message)
        return enclose(payload.SerializeToString())
    
    raise RuntimeError(f"Tag encoding: {tag_encoding} is not supported!")

def uncover_to_text(key: str, value: bytes) -> str:
    received_at, enclosed_at, payload = uncover(value)
    return payload.decode()

def uncover_to_base64(key: str, value: bytes) -> str:
    received_at, enclosed_at, payload = uncover(value)
    return b64encode(payload).decode()

def uncover_to_json(key: str, value: bytes) -> str:
    key = str(key)
    try:
        received_at, enclosed_at, payload = uncover(value)

        tag = get_tag_from_pub_sub_topic(key)
        print(tag)

        if not is_tag_well_known(tag):
            warnings.warn(f"Tag: {tag} is not well-known, assuming it is JSON")
            return payload.decode()
        
        tag_encoding = get_tag_encoding(tag)

        if tag_encoding == "json":
            return payload.decode()
        elif tag_encoding == "protobuf":
            type_name = get_tag_description(tag)
            print(type_name)
            message = decode_protobuf_payload_from_type_name(payload, type_name)
            return json.dumps(MessageToDict(message))

    except Exception as exc:
        print(exc)
        
    raise RuntimeError(f"Tag encoding: {tag_encoding} is not supported!")
