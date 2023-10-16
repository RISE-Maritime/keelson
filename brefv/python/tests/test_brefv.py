import time
import brefv

import brefv.payloads.primitives_pb2 as primitives


def test_enclose_uncover():
    test = b"test"

    message = brefv.enclose(test)

    received_at, enclosed_at, content = brefv.uncover(message)

    assert test == content
    assert received_at >= enclosed_at


def test_enclose_uncover_actual_payload():
    data = primitives.TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14

    message = brefv.enclose(data.SerializeToString())

    received_at, enclosed_at, payload = brefv.uncover(message)

    content = primitives.TimestampedFloat.FromString(payload)

    assert data.value == content.value
    assert data.timestamp == content.timestamp
    assert enclosed_at >= content.timestamp.ToNanoseconds()
    assert received_at >= enclosed_at


def test_decode_protobuf_using_generated_message_classes():
    data = primitives.TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14

    payload = data.SerializeToString()

    decoded = brefv.decode_protobuf_payload_from_type_name(
        payload, "brefv.primitives.TimestampedFloat"
    )

    assert data.value == decoded.value
    assert (
        data.timestamp.ToNanoseconds() == decoded.timestamp.ToNanoseconds()
    )  # These are different class definitions and will fail a direct comparison...


def test_get_descriptor_from_type_name():
    brefv.get_protobuf_descriptor_from_type_name("foxglove.PointCloud")
