import time
from typing import Tuple

from brefv.envelope_pb2 import Envelope


def enclose(payload: bytes) -> bytes:
    env: Envelope = Envelope()
    env.enclosed_at.FromNanoseconds(time.time_ns())
    env.payload = payload

    return env.SerializeToString()


def unwrap(message: bytes) -> Tuple[int, Tuple[int, bytes]]:
    env = Envelope.FromString(message)
    return time.time_ns(), env.enclosed_at.ToNanoseconds(), env.payload
