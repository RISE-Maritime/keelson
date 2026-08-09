"""Unit tests for the three-tier liveliness scaffolding (#130)."""

from unittest.mock import Mock

import pytest

import keelson
from keelson.scaffolding import (
    PubsubSubjectLivelinessManager,
    declare_liveliness,
    declare_pubsub_subject_liveliness,
    declare_rpc_interface_liveliness,
    declare_source_liveliness,
)


@pytest.fixture
def session():
    session = Mock()
    session.liveliness = Mock(
        return_value=Mock(declare_token=Mock(side_effect=lambda key: Mock(key=key)))
    )
    return session


def _declared_keys(session):
    return [
        c.args[0] for c in session.liveliness.return_value.declare_token.call_args_list
    ]


@pytest.mark.unit
def test_source_level_key_shapes():
    assert (
        keelson.construct_source_liveliness_key("realm", "boat", "nmea/0")
        == "realm/@v0/boat/*/nmea/0"
    )
    assert keelson.parse_source_liveliness_key("realm/@v0/boat/*/nmea/0") == dict(
        base_path="realm", entity_id="boat", source_id="nmea/0"
    )


@pytest.mark.unit
def test_parse_source_liveliness_key_rejects_other_tiers():
    for key in (
        "realm/@v0/boat/pubsub/*/nmea/0",  # legacy coarse token
        "realm/@v0/boat/pubsub/location_fix/nmea/0",  # subject token
        "realm/@v0/boat/@rpc/whep_proxy/v1/*/mediamtx/0",  # rpc token
    ):
        with pytest.raises(ValueError):
            keelson.parse_source_liveliness_key(key)


@pytest.mark.unit
def test_declare_source_liveliness_declares_and_undeclares(session):
    with declare_source_liveliness(session, "realm", "boat", "nmea/0") as token:
        assert _declared_keys(session) == ["realm/@v0/boat/*/nmea/0"]
        token.undeclare.assert_not_called()
    token.undeclare.assert_called_once()


@pytest.mark.unit
def test_declare_pubsub_subject_liveliness_one_token_per_subject(session):
    subjects = ["location_fix", "heading_true_deg"]
    with declare_pubsub_subject_liveliness(
        session, "realm", "boat", "nmea/0", subjects
    ) as tokens:
        assert _declared_keys(session) == [
            "realm/@v0/boat/pubsub/location_fix/nmea/0",
            "realm/@v0/boat/pubsub/heading_true_deg/nmea/0",
        ]
        assert len(tokens) == 2
    for token in tokens:
        token.undeclare.assert_called_once()


@pytest.mark.unit
def test_declare_rpc_interface_liveliness(session):
    with declare_rpc_interface_liveliness(
        session, "realm", "boat", "mediamtx/0", "whep_proxy", "v1"
    ) as token:
        assert _declared_keys(session) == [
            "realm/@v0/boat/@rpc/whep_proxy/v1/*/mediamtx/0"
        ]
    token.undeclare.assert_called_once()


@pytest.mark.unit
def test_subject_manager_add_remove_idempotent(session):
    manager = PubsubSubjectLivelinessManager(session, "realm", "boat", "camera/0")
    manager.add("raw_image")
    manager.add("raw_image")  # idempotent
    assert _declared_keys(session) == ["realm/@v0/boat/pubsub/raw_image/camera/0"]
    assert manager.subjects() == {"raw_image"}

    manager.add("compressed_image")
    manager.remove("raw_image")
    manager.remove("raw_image")  # idempotent
    assert manager.subjects() == {"compressed_image"}

    manager.close()
    assert manager.subjects() == set()


@pytest.mark.unit
def test_subject_manager_context_manager_closes(session):
    with PubsubSubjectLivelinessManager(session, "realm", "boat", "cam/0") as manager:
        manager.add("raw_image")
    assert manager.subjects() == set()


@pytest.mark.unit
def test_composite_declare_liveliness(session):
    with declare_liveliness(
        session,
        "realm",
        "boat",
        "mavlink/0",
        pubsub_subjects=["location_fix"],
        rpc_interfaces=[("vehicle_lifecycle", "v1"), ("vehicle_control", "v1")],
    ):
        assert _declared_keys(session) == [
            "realm/@v0/boat/*/mavlink/0",
            "realm/@v0/boat/pubsub/location_fix/mavlink/0",
            "realm/@v0/boat/@rpc/vehicle_lifecycle/v1/*/mavlink/0",
            "realm/@v0/boat/@rpc/vehicle_control/v1/*/mavlink/0",
        ]
