import time
import keelson

from keelson.payloads.Primitives_pb2 import TimestampedFloat


def test_construct_pub_sub_key():
    assert (
        keelson.construct_pubsub_key(
            base_path="base_path",
            entity_id="entity_id",
            subject="subject",
            source_id="source_id",
        )
        == "base_path/@v0/entity_id/pubsub/subject/source_id"
    )


def test_construct_rpc_key():
    assert (
        keelson.construct_rpc_key(
            base_path="base_path",
            entity_id="entity_id",
            procedure="procedure",
            source_id="source_id",
        )
        == "base_path/@v0/entity_id/@rpc/procedure/source_id"
    )


def test_parse_pub_sub_key():
    assert keelson.parse_pubsub_key(
        "base_path/@v0/entity_id/pubsub/subject/source_id/sub_id"
    ) == dict(
        base_path="base_path",
        entity_id="entity_id",
        subject="subject",
        source_id="source_id/sub_id",
    )


def test_parse_rpc_key():
    assert keelson.parse_rpc_key(
        "base_path/@v0/entity_id/@rpc/procedure/source_id"
    ) == dict(
        base_path="base_path",
        entity_id="entity_id",
        procedure="procedure",
        source_id="source_id",
    )


def test_get_subject_from_pub_sub_key():
    assert (
        keelson.get_subject_from_pubsub_key(
            "base_path/@v0/entity_id/pubsub/subject/source_id"
        )
        == "subject"
    )


def test_enclose_uncover():
    test = b"test"
    message = keelson.enclose(payload=test)
    enclosed_at, received_at, payload = keelson.uncover(message)

    assert test == payload
    assert received_at >= enclosed_at


def test_enclose_uncover_actual_payload():
    data = TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14
    message = keelson.enclose(data.SerializeToString())
    enclosed_at, received_at, payload = keelson.uncover(message)
    content = TimestampedFloat.FromString(payload)

    assert data.value == content.value
    assert data.timestamp == content.timestamp
    assert received_at >= enclosed_at


def test_get_protobuf_file_descriptor_set_from_type_name():
    file_descriptor_set = keelson.get_protobuf_file_descriptor_set_from_type_name(
        "keelson.primitives.TimestampedString"
    )
    assert file_descriptor_set


def test_decode_protobuf_using_generated_message_classes():
    data = TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14

    payload = data.SerializeToString()

    decoded = keelson.decode_protobuf_payload_from_type_name(
        payload, "keelson.primitives.TimestampedFloat"
    )

    assert data.value == decoded.value
    assert (
        data.timestamp.ToNanoseconds()
        == decoded.timestamp.ToNanoseconds()
        # These are different class definitions and will fail a direct comparison...
    )


def test_ensure_all_well_known_tags():
    for subject, value in keelson._SUBJECTS.items():
        assert subject == str(subject).lower()

        schema = value["schema"]

        assert keelson.get_protobuf_file_descriptor_set_from_type_name(schema)


def test_is_subject_well_known():
    assert keelson.is_subject_well_known("lever_position_pct") == True
    assert keelson.is_subject_well_known("random_mumbo_jumbo") == False


def test_get_subject_schema():
    assert (
        keelson.get_subject_schema("lever_position_pct")
        == "keelson.primitives.TimestampedFloat"
    )


def test_subpackages_importability():
    from keelson.payloads.foxglove.PointCloud_pb2 import PointCloud
    from keelson.payloads.ImuReading_pb2 import ImuReading


def test_interfaces_importability():
    from keelson.interfaces.WHEPProxy_pb2 import WHEPRequest, WHEPResponse
