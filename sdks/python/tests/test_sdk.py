import time
import json
import keelson

from keelson.payloads.TimestampedFloat_pb2 import TimestampedFloat
from keelson.payloads.TimestampedString_pb2 import TimestampedString


def test_construct_pub_sub_key():
    assert (
        keelson.construct_pub_sub_key(
            realm="realm",
            entity_id="entity_id",
            subject="subject",
            source_id="source_id",
        )
        == "realm/v0/entity_id/pubsub/subject/source_id"
    )


def test_construct_req_rep_key():
    assert (
        keelson.construct_req_rep_key(
            realm="realm",
            entity_id="entity_id",
            procedure="procedure",
            target_id="target_id",
        )
        == "realm/v0/entity_id/rpc/procedure/target_id"
    )


def test_parse_pub_sub_key():
    assert keelson.parse_pub_sub_key(
        "realm/v0/entity_id/pubsub/subject/source_id/sub_id"
    ) == dict(
        realm="realm",
        entity_id="entity_id",
        subject="subject",
        source_id="source_id/sub_id",
    )

def test_parse_req_rep_key():
    assert keelson.parse_req_rep_key(
        "realm/v0/entity_id/rpc/procedure/target_id/sub_id"
    ) == dict(
        realm="realm",
        entity_id="entity_id",
        procedure="procedure",
        target_id="target_id/sub_id",
    )


def test_get_subject_from_pub_sub_key():
    assert (
        keelson.get_subject_from_pub_sub_key(
            "realm/v0/entity_id/pubsub/subject/source_id"
        )
        == "subject"
    )

def test_get_procedure_from_req_rep_key():
    assert (
        keelson.get_procedure_from_req_rep_key(
            "realm/v0/entity_id/rpc/procedure/target_id"
        )
        == "procedure"
    )


def test_enclose_uncover():
    test = b"test"
    message = keelson.enclose(payload=test)
    envelope_obj = keelson.uncover(message)

    assert test == envelope_obj.payload
    assert envelope_obj.received_at >= envelope_obj.enclosed_at


def test_enclose_uncover_actual_payload():
    data = TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14

    message = keelson.enclose(data)
    envelope_obj = keelson.uncover(message)
    content = TimestampedFloat.FromString(envelope_obj.payload)

    assert data.value == content.value
    assert data.timestamp == content.timestamp
    assert envelope_obj.enclosed_at >= content.timestamp.ToNanoseconds()
    assert envelope_obj.received_at >= envelope_obj.enclosed_at


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
        data.timestamp.ToNanoseconds() == decoded.timestamp.ToNanoseconds()
    )  # These are different class definitions and will fail a direct comparison...


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
    from keelson.payloads.PointCloud_pb2 import PointCloud
    from keelson.payloads.ImuReading_pb2 import ImuReading


def test_req_rep_key():

    req_rep_key = keelson.construct_req_rep_key(
        realm="realm",
        entity_id="entity_id",
        procedure="procedure",
        target_id="target_id",
    )


    parsed_req_rep_key = keelson.parse_req_rep_key(req_rep_key)

    assert parsed_req_rep_key == {
        "realm": "realm",
        "entity_id": "entity_id",
        "procedure": "procedure",
        "target_id": "target_id",
    }