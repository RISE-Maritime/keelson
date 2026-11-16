"""The replayer's loopback guard (#309 review).

`mcap2keelson` refuses a file that holds channels on its own pubsub keys:
replaying them verbatim would publish onto its own key. The guard used to be a
substring test (`/@v0/{entity}/pubsub/` in the topic, topic ends in
`/{source_id}`), so it also refused any other source under its entity whose id
merely ended in `/0` -- `location_fix/gnss/0`, or a recorder's
`configuration_json/recorder/0`. A recording made with a wide `-k` on the
replayer's entity was then refused outright.
"""

import argparse
import sys
import time
from importlib import import_module
from pathlib import Path

import pytest
from mcap.reader import make_reader


bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

mcap2keelson = import_module("mcap2keelson")
_own_channels = mcap2keelson._own_channels

REALM = "rise"
ENTITY = "station"
SOURCE = "0"
OWN = f"{REALM}/@v0/{ENTITY}/pubsub/replay_status/{SOURCE}"


class TestOwnChannels:
    def test_the_exact_own_key_is_refused(self):
        assert _own_channels([OWN], REALM, ENTITY, SOURCE) == [OWN]

    def test_a_multi_chunk_source_ending_in_the_same_id_is_not_ours(self):
        topics = [
            f"{REALM}/@v0/{ENTITY}/pubsub/location_fix/gnss/0",
            f"{REALM}/@v0/{ENTITY}/pubsub/configuration_json/recorder/0",
        ]
        assert _own_channels(topics, REALM, ENTITY, SOURCE) == []

    def test_another_realm_or_entity_is_not_ours(self):
        topics = [
            f"other/@v0/{ENTITY}/pubsub/replay_status/{SOURCE}",
            f"{REALM}/@v0/boat/pubsub/replay_status/{SOURCE}",
            f"{REALM}/@v0/{ENTITY}x/pubsub/replay_status/{SOURCE}",
        ]
        assert _own_channels(topics, REALM, ENTITY, SOURCE) == []

    def test_a_targeted_key_on_our_source_is_ours(self):
        topic = f"{OWN}/@target/mmsi_265123456"
        assert _own_channels([topic], REALM, ENTITY, SOURCE) == [topic]

    def test_non_keelson_topics_are_ignored(self):
        topics = ["/camera/image", "not-a-key", f"{REALM}/@v0/{ENTITY}/@rpc/x/v1/y/0"]
        assert _own_channels(topics, REALM, ENTITY, SOURCE) == []

    def test_result_is_sorted(self):
        a = f"{REALM}/@v0/{ENTITY}/pubsub/a_subject/{SOURCE}"
        b = f"{REALM}/@v0/{ENTITY}/pubsub/b_subject/{SOURCE}"
        assert _own_channels([b, a], REALM, ENTITY, SOURCE) == [a, b]


@pytest.mark.e2e
def test_a_wide_recording_on_the_replayers_entity_loads(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Fredrik's reproduction: recorder and replayer on one entity, the
    recorder's -k covering that entity. The recorder publishes its own key set
    on `configuration_json` at startup and records it, so the file holds a key
    under the replayer's entity whose source ends in `/0`. The replayer must
    still load it.

    `-k` must name `@v0`: a `**` never matches a verbatim `@` chunk, so
    `rise/**` would record nothing at all.
    """
    out = temp_dir / "rec"
    out.mkdir()
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--entity-id",
            ENTITY,
            "-k",
            f"{REALM}/@v0/**",
            "--output-folder",
            str(out),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
            "--bypass-safeguards",
        ],
    )
    recorder.start()
    time.sleep(2)
    recorder.stop()

    files = list(out.glob("*.mcap"))
    assert len(files) == 1, files
    with files[0].open("rb") as f:
        topics = {c.topic for c in make_reader(f).get_summary().channels.values()}
    config_topic = f"{REALM}/@v0/{ENTITY}/pubsub/configuration_json/recorder/0"
    assert config_topic in topics, f"the scenario needs that key: {topics}"

    # The substring test this replaces refused exactly this topic.
    assert f"/@v0/{ENTITY}/pubsub/" in config_topic and config_topic.endswith(
        f"/{SOURCE}"
    )

    args = argparse.Namespace(
        realm=REALM, entity_id=ENTITY, source_id=SOURCE, replay_key_tag=False
    )
    handle = mcap2keelson._open_one(files[0], args, lambda _pct: None)
    handle["fh"].close()
