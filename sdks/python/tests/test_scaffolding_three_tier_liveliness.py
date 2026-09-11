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
    subject_liveliness_keys,
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


# --- the target-scoped form (#253) ------------------------------------------
#
# A source publishing about OTHER entities declares a second token per subject,
# carrying `@target/*`. Both forms, never one: `@target` is verbatim, so a
# source holding only the target form is invisible to every existing discovery
# query, and one holding only the plain form advertises a key it never writes.


@pytest.mark.unit
def test_untargeted_is_exactly_what_it_was():
    """The default is unchanged, which is the whole compatibility claim at the
    key-building level."""
    assert subject_liveliness_keys("realm", "boat", "location_fix", "gnss/0") == [
        "realm/@v0/boat/pubsub/location_fix/gnss/0"
    ]


@pytest.mark.unit
def test_targeted_declares_both_forms():
    assert subject_liveliness_keys(
        "realm", "shore", "location_fix", "ais", targeted=True
    ) == [
        "realm/@v0/shore/pubsub/location_fix/ais",
        "realm/@v0/shore/pubsub/location_fix/ais/@target/*",
    ]


@pytest.mark.unit
def test_a_multi_level_source_id_survives():
    """Not hypothetical: `maritimedb` publishes AIS under
    `srv-herakles/sjofartsverket` on the rise bus, and the key is built through
    `construct_pubsub_key` rather than assembled so this cannot drift."""
    plain, targeted = subject_liveliness_keys(
        "rise", "maritimedb", "name", "srv-herakles/sjofartsverket", targeted=True
    )
    assert plain == "rise/@v0/maritimedb/pubsub/name/srv-herakles/sjofartsverket"
    assert targeted == plain + "/@target/*"


@pytest.mark.unit
def test_declare_pubsub_subject_liveliness_targeted(session):
    subjects = ["location_fix", "name"]
    with declare_pubsub_subject_liveliness(
        session, "realm", "shore", "ais", subjects, targeted=True
    ) as tokens:
        assert _declared_keys(session) == [
            "realm/@v0/shore/pubsub/location_fix/ais",
            "realm/@v0/shore/pubsub/location_fix/ais/@target/*",
            "realm/@v0/shore/pubsub/name/ais",
            "realm/@v0/shore/pubsub/name/ais/@target/*",
        ]
        assert len(tokens) == 4
    for token in tokens:
        token.undeclare.assert_called_once()


@pytest.mark.unit
def test_subject_manager_targeted_adds_and_removes_both(session):
    manager = PubsubSubjectLivelinessManager(
        session, "realm", "shore", "ais", targeted=True
    )
    manager.add("location_fix")
    manager.add("location_fix")  # still idempotent
    assert _declared_keys(session) == [
        "realm/@v0/shore/pubsub/location_fix/ais",
        "realm/@v0/shore/pubsub/location_fix/ais/@target/*",
    ]
    assert manager.subjects() == {"location_fix"}

    # A subject is one entry however many tokens back it — removing it must
    # take both, or the target form outlives the capability it advertises.
    tokens = list(manager._tokens["location_fix"])
    manager.remove("location_fix")
    assert manager.subjects() == set()
    for token in tokens:
        token.undeclare.assert_called_once()


@pytest.mark.unit
def test_composite_declare_liveliness_targeted(session):
    with declare_liveliness(
        session,
        "realm",
        "shore",
        "ais",
        pubsub_subjects=["location_fix"],
        targeted=True,
    ):
        assert _declared_keys(session) == [
            "realm/@v0/shore/*/ais",
            "realm/@v0/shore/pubsub/location_fix/ais",
            "realm/@v0/shore/pubsub/location_fix/ais/@target/*",
        ]
