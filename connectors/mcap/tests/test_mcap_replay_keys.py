"""Unit tests for how mcap2keelson names the keys it republishes.

A replay republishes recorded keys under ``rec-<recorded entity>``
(docs/protocol-specification.md, entity_id). The expected keys below are
written out literally on purpose: they pin the wire shape, so they must not be
computed with the function under test.
"""

import sys
from importlib import import_module
from pathlib import Path

import pytest

bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

mcap2keelson = import_module("mcap2keelson")
replay_key = mcap2keelson.replay_key
is_keelson_key = mcap2keelson.is_keelson_key
loopback_collisions = mcap2keelson.loopback_collisions


@pytest.mark.unit
class TestReplayKey:
    def test_default_prefix_is_rec(self):
        assert mcap2keelson.DEFAULT_ENTITY_PREFIX == "rec-"

    def test_prefixes_the_entity_chunk_only(self):
        assert (
            replay_key("rise/@v0/sf18/pubsub/location_fix/gnss/0", "rec-")
            == "rise/@v0/rec-sf18/pubsub/location_fix/gnss/0"
        )

    def test_source_chunk_is_untouched(self):
        # The source names the producer; the replay never tags it.
        assert (
            replay_key("rise/@v0/vessel/pubsub/heading_true_north_deg/sim", "rec-")
            == "rise/@v0/rec-vessel/pubsub/heading_true_north_deg/sim"
        )

    def test_simulated_entity_becomes_rec_sim(self):
        assert (
            replay_key("rise/@v0/sim-usv-alpha/pubsub/location_fix/slipway", "rec-")
            == "rise/@v0/rec-sim-usv-alpha/pubsub/location_fix/slipway"
        )

    def test_idempotent_on_an_already_prefixed_entity(self):
        key = "rise/@v0/rec-sf18/pubsub/location_fix/gnss"
        assert replay_key(key, "rec-") == key

    def test_applying_twice_equals_applying_once(self):
        once = replay_key("rise/@v0/sf18/pubsub/location_fix/gnss", "rec-")
        assert replay_key(once, "rec-") == once
        assert "rec-rec-" not in once

    def test_target_keys_are_rewritten_and_keep_the_target(self):
        assert (
            replay_key(
                "rise/@v0/shore_station/pubsub/location_fix/ais/@target/mmsi_245060000",
                "rec-",
            )
            == "rise/@v0/rec-shore_station/pubsub/location_fix/ais/@target/mmsi_245060000"
        )

    def test_rpc_keys_are_rewritten(self):
        assert (
            replay_key("rise/@v0/sf18/@rpc/vehicle_mode/v1/arm/mavlink", "rec-")
            == "rise/@v0/rec-sf18/@rpc/vehicle_mode/v1/arm/mavlink"
        )

    def test_empty_prefix_is_verbatim(self):
        key = "rise/@v0/sf18/pubsub/location_fix/gnss"
        assert replay_key(key, "") == key

    def test_custom_prefix(self):
        assert (
            replay_key("rise/@v0/sf18/pubsub/location_fix/gnss", "trial-")
            == "rise/@v0/trial-sf18/pubsub/location_fix/gnss"
        )

    def test_prefix_inside_the_entity_is_not_a_match(self):
        # Only a leading prefix counts as "already prefixed".
        assert (
            replay_key("rise/@v0/my-rec-boat/pubsub/x/0", "rec-")
            == "rise/@v0/rec-my-rec-boat/pubsub/x/0"
        )

    def test_realm_is_untouched(self):
        assert (
            replay_key("rec-realm/@v0/sf18/pubsub/x/0", "rec-")
            == "rec-realm/@v0/rec-sf18/pubsub/x/0"
        )

    def test_a_multi_chunk_realm_is_prefixed(self):
        """The realm may span chunks (keelson.parse_pubsub_key accepts it). Such a
        key must still be prefixed, or the replay lands on the live keys."""
        assert (
            replay_key("rise/masslab/@v0/sf18/pubsub/x/0", "rec-")
            == "rise/masslab/@v0/rec-sf18/pubsub/x/0"
        )

    @pytest.mark.parametrize(
        "topic",
        [
            "/foxglove/raw",
            "some/plain/topic",
            "rise/sf18/pubsub/x/0",  # no @v0
            "@v0/sf18/pubsub/x/0",  # no realm
            "rise/@v0",  # no entity chunk
            "rise/@v0//pubsub/x/0",  # empty entity chunk
            "",
        ],
    )
    def test_non_keelson_topics_are_unchanged(self, topic):
        assert not is_keelson_key(topic)
        assert replay_key(topic, "rec-") == topic


@pytest.mark.unit
class TestLoopbackCollisions:
    def test_default_prefix_cannot_collide_with_an_unprefixed_daemon(self):
        own = "rise/@v0/replayer/pubsub/raw/0"
        assert loopback_collisions([own], "rise", "replayer", "0", "rec-") == []

    def test_empty_prefix_collides_with_the_daemons_own_key(self):
        own = "rise/@v0/replayer/pubsub/raw/0"
        assert loopback_collisions([own], "rise", "replayer", "0", "") == [own]

    def test_prefixed_daemon_collides_after_the_rewrite(self):
        # The recorded key is NOT the daemon's key, but its replayed key is.
        recorded = "rise/@v0/replayer/pubsub/raw/0"
        assert loopback_collisions([recorded], "rise", "rec-replayer", "0", "rec-") == [
            recorded
        ]

    def test_prefixed_daemon_collides_with_an_already_prefixed_recording(self):
        recorded = "rise/@v0/rec-replayer/pubsub/raw/0"
        assert loopback_collisions([recorded], "rise", "rec-replayer", "0", "rec-") == [
            recorded
        ]

    def test_a_multi_chunk_source_ending_in_the_same_id_does_not_collide(self):
        topics = [
            "rise/@v0/replayer/pubsub/location_fix/gnss/0",
            "rise/@v0/replayer/pubsub/configuration_json/recorder/0",
        ]
        assert loopback_collisions(topics, "rise", "replayer", "0", "") == []

    def test_a_targeted_key_on_the_daemons_source_collides(self):
        topic = "rise/@v0/replayer/pubsub/raw/0/@target/mmsi_265123456"
        assert loopback_collisions([topic], "rise", "replayer", "0", "") == [topic]

    def test_an_rpc_key_does_not_collide(self):
        topic = "rise/@v0/replayer/@rpc/replay_control/v1/play/0"
        assert loopback_collisions([topic], "rise", "replayer", "0", "") == []

    def test_other_source_does_not_collide(self):
        assert (
            loopback_collisions(
                ["rise/@v0/replayer/pubsub/raw/1"], "rise", "replayer", "0", ""
            )
            == []
        )

    def test_other_realm_does_not_collide(self):
        assert (
            loopback_collisions(
                ["other/@v0/replayer/pubsub/raw/0"], "rise", "replayer", "0", ""
            )
            == []
        )

    def test_non_keelson_topic_does_not_collide(self):
        assert loopback_collisions(["/raw/0"], "rise", "replayer", "0", "") == []

    def test_collisions_are_sorted(self):
        topics = ["rise/@v0/replayer/pubsub/b/0", "rise/@v0/replayer/pubsub/a/0"]
        assert loopback_collisions(topics, "rise", "replayer", "0", "") == sorted(
            topics
        )


@pytest.mark.unit
class TestEntityPrefixArgument:
    def test_default(self):
        args = mcap2keelson._build_parser().parse_args([])
        assert args.entity_prefix == "rec-"

    def test_empty_opts_out(self):
        args = mcap2keelson._build_parser().parse_args(["--entity-prefix", ""])
        assert args.entity_prefix == ""

    def test_replay_key_tag_is_removed_with_a_pointer_to_entity_prefix(self, capsys):
        with pytest.raises(SystemExit) as exc:
            mcap2keelson._build_parser().parse_args(["--replay-key-tag"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--replay-key-tag was removed" in err
        assert "--entity-prefix" in err

    def test_replay_key_tag_is_not_advertised(self):
        assert "--replay-key-tag" not in mcap2keelson._build_parser().format_help()
