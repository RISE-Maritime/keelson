import json
import time
from pathlib import Path
from typing import Tuple

from google.protobuf.message import Message

from brefv.messages.envelope_pb2 import Envelope


THIS_DIR = Path(__file__).parent

TAG_TO_TYPE_MAP = json.loads((THIS_DIR / "tag_type_map.json").read_text())


def message_name_from_tag(tag: str) -> str:
    return TAG_TO_TYPE_MAP["tags"][tag]


def instance_from_message_name(name: str) -> Message:


def enclose(payload: bytes) -> bytes:
    env: Envelope = Envelope()
    env.enclosed_at.FromNanoseconds(time.time_ns())
    env.payload = payload

    return env.SerializeToString()


def unwrap(message: bytes) -> Tuple[int, Tuple[int, bytes]]:
    env = Envelope.FromString(message)
    return time.time_ns(), (env.enclosed_at.ToNanoseconds(), env.payload)
