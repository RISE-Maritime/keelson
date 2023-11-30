import time
import json
import brefv

import brefv.payloads.primitives_pb2 as primitives


def test_construct_pub_sub_topic():
    assert (
        brefv.construct_pub_sub_topic(
            realm="realm",
            entity_id="entity_id",
            interface_type="interface_type",
            interface_id="interface_id",
            tag="tag",
            source_id="source_id",
        )
        == "realm/entity_id/interface_type/interface_id/tag/source_id"
    )


def test_construct_req_rep_topic():
    assert (
        brefv.construct_req_rep_topic(
            realm="realm",
            entity_id="entity_id",
            interface_type="interface_type",
            interface_id="interface_id",
            procedure="procedure",
        )
        == "realm/entity_id/interface_type/interface_id/rpc/procedure"
    )


def test_parse_pub_sub_topic():
    assert brefv.parse_pub_sub_topic(
        "realm/entity_id/interface_type/interface_id/tag/source_id/sub_id"
    ) == dict(
        realm="realm",
        entity_id="entity_id",
        interface_type="interface_type",
        interface_id="interface_id",
        tag="tag",
        source_id="source_id/sub_id",
    )


def test_get_tag_from_pub_sub_topic():
    assert (
        brefv.get_tag_from_pub_sub_topic(
            "realm/entity_id/interface_type/interface_id/tag/source_id"
        )
        == "tag"
    )


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


def test_get_protobuf_file_descriptor_set_from_type_name():
    file_descriptor_set = brefv.get_protobuf_file_descriptor_set_from_type_name(
        "brefv.primitives.TimestampedString"
    )
    assert file_descriptor_set


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

def test_ensure_all_well_known_tags():

    for tag, value in brefv._TAGS.items():

        assert tag == str(tag).lower()

        encoding = value["encoding"]
        description = value["description"]

        match encoding:
            case "protobuf":
                assert brefv.get_protobuf_file_descriptor_set_from_type_name(description)
            case "json":
                assert json.dumps(description)

def test_is_tag_well_known():
    assert brefv.is_tag_well_known("lever_position_pct") == True
    assert brefv.is_tag_well_known("random_mumbo_jumbo") == False


def test_get_tag_encoding():
    assert brefv.get_tag_encoding("lever_position_pct") == "protobuf"


def test_get_tag_encoding():
    assert (
        brefv.get_tag_description("lever_position_pct")
        == "brefv.primitives.TimestampedFloat"
    )


def test_subpackages_importability():
    from brefv.payloads.foxglove.PointCloud_pb2 import PointCloud
    from brefv.payloads.compound.ImuReading_pb2 import ImuReading
