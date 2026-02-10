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
            responder_id="responder_id",
        )
        == "base_path/@v0/entity_id/@rpc/procedure/responder_id"
    )


def test_parse_pub_sub_key():
    assert keelson.parse_pubsub_key(
        "base_path/@v0/entity_id/pubsub/subject/source_id/sub_id"
    ) == dict(
        base_path="base_path",
        entity_id="entity_id",
        subject="subject",
        source_id="source_id/sub_id",
        target_id=None,
    )


def test_parse_rpc_key():
    assert keelson.parse_rpc_key(
        "base_path/@v0/entity_id/@rpc/procedure/responder_id"
    ) == dict(
        base_path="base_path",
        entity_id="entity_id",
        procedure="procedure",
        responder_id="responder_id",
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
    received_at, enclosed_at, payload = keelson.uncover(message)

    assert test == payload
    assert received_at >= enclosed_at


def test_enclose_uncover_actual_payload():
    data = TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14
    message = keelson.enclose(data.SerializeToString())
    received_at, enclosed_at, payload = keelson.uncover(message)
    content = TimestampedFloat.FromString(payload)

    assert data.value == content.value
    assert data.timestamp == content.timestamp
    assert received_at >= enclosed_at


def test_get_protobuf_file_descriptor_set_from_type_name():
    file_descriptor_set = keelson.get_protobuf_file_descriptor_set_from_type_name(
        "keelson.TimestampedString"
    )
    assert file_descriptor_set


def test_decode_protobuf_using_generated_message_classes():
    data = TimestampedFloat()
    data.timestamp.FromNanoseconds(time.time_ns())
    data.value = 3.14

    payload = data.SerializeToString()

    decoded = keelson.decode_protobuf_payload_from_type_name(
        payload, "keelson.TimestampedFloat"
    )

    assert data.value == decoded.value
    assert (
        data.timestamp.ToNanoseconds()
        == decoded.timestamp.ToNanoseconds()
        # These are different class definitions and will fail a direct comparison...
    )


def test_ensure_all_well_known_tags():
    for subject, proto_type_name in keelson._SUBJECTS.items():
        assert subject == str(subject).lower()

        assert keelson.get_protobuf_file_descriptor_set_from_type_name(proto_type_name)


def test_is_subject_well_known():
    assert keelson.is_subject_well_known("lever_position_pct") is True
    assert keelson.is_subject_well_known("random_mumbo_jumbo") is False


def test_get_subject_schema():
    assert (
        keelson.get_subject_schema("lever_position_pct") == "keelson.TimestampedFloat"
    )


def test_construct_liveliness_key():
    assert (
        keelson.construct_liveliness_key(
            base_path="keelson",
            entity_id="landkrabban",
            source_id="gnss/0",
        )
        == "keelson/@v0/landkrabban/pubsub/*/gnss/0"
    )


def test_parse_liveliness_key():
    key = keelson.construct_liveliness_key(
        base_path="keelson",
        entity_id="landkrabban",
        source_id="gnss/0",
    )
    parsed = keelson.parse_liveliness_key(key)
    assert parsed == dict(
        base_path="keelson",
        entity_id="landkrabban",
        source_id="gnss/0",
    )


def test_parse_liveliness_key_with_slashed_source():
    parsed = keelson.parse_liveliness_key("keelson/@v0/landkrabban/pubsub/*/gnss/0")
    assert parsed["source_id"] == "gnss/0"


def test_parse_liveliness_key_invalid():
    import pytest

    with pytest.raises(ValueError):
        keelson.parse_liveliness_key("keelson/@v0/entity/pubsub/some_subject/source")


def test_subpackages_importability():
    pass


def test_interfaces_importability():
    pass


# Tests for @target extension


def test_construct_pubsub_key_with_target_id():
    """Test constructing a pubsub key with target_id."""
    assert (
        keelson.construct_pubsub_key(
            base_path="keelson",
            entity_id="shore_station",
            subject="heading_true_north_deg",
            source_id="ais",
            target_id="mmsi_245060000",
        )
        == "keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000"
    )


def test_construct_pubsub_key_with_target_id_none():
    """Test that target_id=None produces same result as omitting it."""
    key_without = keelson.construct_pubsub_key(
        base_path="keelson",
        entity_id="entity",
        subject="subject",
        source_id="source",
    )
    key_with_none = keelson.construct_pubsub_key(
        base_path="keelson",
        entity_id="entity",
        subject="subject",
        source_id="source",
        target_id=None,
    )
    assert key_without == key_with_none
    assert "@target" not in key_without


def test_construct_pubsub_key_with_slashed_source_and_target():
    """Test constructing a pubsub key with nested source_id and target_id."""
    assert (
        keelson.construct_pubsub_key(
            base_path="keelson",
            entity_id="vessel",
            subject="location_fix",
            source_id="ais/receiver/0",
            target_id="mmsi_123456789",
        )
        == "keelson/@v0/vessel/pubsub/location_fix/ais/receiver/0/@target/mmsi_123456789"
    )


def test_parse_pubsub_key_with_target_id():
    """Test parsing a pubsub key with @target extension."""
    parsed = keelson.parse_pubsub_key(
        "keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000"
    )
    assert parsed == dict(
        base_path="keelson",
        entity_id="shore_station",
        subject="heading_true_north_deg",
        source_id="ais",
        target_id="mmsi_245060000",
    )


def test_parse_pubsub_key_without_target_id():
    """Test parsing a pubsub key without @target extension returns target_id=None."""
    parsed = keelson.parse_pubsub_key(
        "keelson/@v0/entity/pubsub/subject/source"
    )
    assert parsed == dict(
        base_path="keelson",
        entity_id="entity",
        subject="subject",
        source_id="source",
        target_id=None,
    )


def test_parse_pubsub_key_with_slashed_source_and_target():
    """Test parsing a pubsub key with nested source_id and target_id."""
    parsed = keelson.parse_pubsub_key(
        "keelson/@v0/vessel/pubsub/location_fix/ais/receiver/0/@target/mmsi_123456789"
    )
    assert parsed == dict(
        base_path="keelson",
        entity_id="vessel",
        subject="location_fix",
        source_id="ais/receiver/0",
        target_id="mmsi_123456789",
    )


def test_pubsub_key_roundtrip_with_target():
    """Test that construct -> parse -> construct produces identical key."""
    original_key = keelson.construct_pubsub_key(
        base_path="keelson",
        entity_id="shore_station",
        subject="speed_over_ground_knots",
        source_id="ais/receiver",
        target_id="mmsi_987654321",
    )
    parsed = keelson.parse_pubsub_key(original_key)
    reconstructed_key = keelson.construct_pubsub_key(
        base_path=parsed["base_path"],
        entity_id=parsed["entity_id"],
        subject=parsed["subject"],
        source_id=parsed["source_id"],
        target_id=parsed["target_id"],
    )
    assert original_key == reconstructed_key


def test_pubsub_key_roundtrip_without_target():
    """Test that construct -> parse -> construct produces identical key (no target)."""
    original_key = keelson.construct_pubsub_key(
        base_path="keelson",
        entity_id="landkrabban",
        subject="location_fix",
        source_id="gnss/0",
    )
    parsed = keelson.parse_pubsub_key(original_key)
    reconstructed_key = keelson.construct_pubsub_key(
        base_path=parsed["base_path"],
        entity_id=parsed["entity_id"],
        subject=parsed["subject"],
        source_id=parsed["source_id"],
        target_id=parsed["target_id"],
    )
    assert original_key == reconstructed_key


def test_get_subject_from_pubsub_key_with_target():
    """Test get_subject_from_pubsub_key works with @target extension."""
    assert (
        keelson.get_subject_from_pubsub_key(
            "keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000"
        )
        == "heading_true_north_deg"
    )
