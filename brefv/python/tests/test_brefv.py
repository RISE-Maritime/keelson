import time
import brefv

import brefv.messages.primitives_pb2 as primitives


def test_enclose():
    test = b"test"

    message = brefv.enclose(test)

    received_at, enclosed_at, content = brefv.unwrap(message)

    assert test == content
    assert received_at >= enclosed_at


def test_actual_payload():
    data = primitives.TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14

    message = brefv.enclose(data.SerializeToString())

    received_at, enclosed_at, payload = brefv.unwrap(message)

    content = primitives.TimestampedFloat.FromString(payload)

    assert data.value == content.value
    assert data.timestamp == content.timestamp
    assert enclosed_at >= content.timestamp.ToNanoseconds()
    assert received_at >= enclosed_at
