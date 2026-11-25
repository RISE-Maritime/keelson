"""Unit tests for the recorder's key set: validation, exclusion and live swaps.

No Zenoh session: `KeySet` takes its declare/undeclare functions as arguments,
so these tests record the calls instead of opening subscriptions.
"""

import sys
import json
from pathlib import Path
from importlib import import_module

import pytest
from mcap.reader import make_reader

bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

keelson2mcap = import_module("keelson2mcap")
KeySet = keelson2mcap.KeySet
validate_key_set = keelson2mcap.validate_key_set
MCAPRotatingWriter = keelson2mcap.MCAPRotatingWriter

ENTITY = "rise/@v0/boat"
LOGS = f"{ENTITY}/pubsub/log_message/**"


class FakeSubscriptions:
    """Stands in for session.declare_subscriber / Subscriber.undeclare."""

    def __init__(self, fail_on=()):
        self.active = set()
        self.fail_on = set(fail_on)

    def declare(self, key):
        if key in self.fail_on:
            raise RuntimeError(f"cannot subscribe to {key}")
        self.active.add(key)
        return key

    def undeclare(self, handle):
        self.active.discard(handle)


def make_key_set(keys, exclude_keys=(), reconfigurable=True, subs=None):
    subs = subs or FakeSubscriptions()
    key_set = KeySet(
        list(keys),
        list(exclude_keys),
        declare=subs.declare,
        undeclare=subs.undeclare,
        reconfigurable=reconfigurable,
    )
    key_set.start()
    return key_set, subs


# =============================================================================
# Validation
# =============================================================================


class TestValidateKeySet:

    def test_accepts_keys_and_exclusions(self):
        assert validate_key_set({"keys": [f"{ENTITY}/**"], "exclude_keys": [LOGS]}) == (
            (f"{ENTITY}/**",),
            (LOGS,),
        )

    def test_exclude_keys_is_optional(self):
        assert validate_key_set({"keys": ["a/b"]}) == (("a/b",), ())

    def test_duplicates_are_dropped_in_order(self):
        assert validate_key_set({"keys": ["b", "a", "b"]}) == (("b", "a"), ())

    @pytest.mark.parametrize(
        "doc, match",
        [
            (["a/b"], "JSON object"),
            ("a/b", "JSON object"),
            ({"exclude_keys": []}, "missing 'keys'"),
            ({"keys": []}, "at least one"),
            ({"keys": "a/b"}, "'keys' must be a list"),
            ({"keys": ["a/b", 3]}, "'keys' must be a list"),
            ({"keys": ["a/b"], "exclude_keys": "x"}, "'exclude_keys' must be a list"),
            ({"keys": ["a/b"], "exclude": ["x"]}, "unknown field"),
            ({"keys": ["a//b"]}, "not a valid key expression"),
            ({"keys": ["a/b"], "exclude_keys": ["a/*b"]}, "not a valid key expression"),
        ],
    )
    def test_rejects(self, doc, match):
        with pytest.raises(ValueError, match=match):
            validate_key_set(doc)

    def test_invalid_key_error_does_not_leak_zenoh_source_path(self):
        with pytest.raises(ValueError) as exc:
            validate_key_set({"keys": ["a//b"]})
        assert ".rs:" not in str(exc.value)


# =============================================================================
# Admission
# =============================================================================


class TestAdmits:

    def test_exclusion_wins_over_inclusion(self):
        key_set, _ = make_key_set([f"{ENTITY}/**"], [LOGS])
        snap = key_set.snapshot
        assert snap.admits(f"{ENTITY}/pubsub/location_fix/gnss/0")
        assert not snap.admits(f"{ENTITY}/pubsub/log_message/docker/ntrip")

    def test_single_chunk_wildcard(self):
        key_set, _ = make_key_set([f"{ENTITY}/**"], [f"{ENTITY}/pubsub/*/secret"])
        snap = key_set.snapshot
        assert not snap.admits(f"{ENTITY}/pubsub/log_message/secret")
        assert snap.admits(f"{ENTITY}/pubsub/log_message/secret/deeper")

    def test_key_outside_every_include_is_not_admitted(self):
        # A sample still queued from a subscription that was just removed.
        key_set, _ = make_key_set([f"{ENTITY}/pubsub/heading_true_north_deg/**"])
        assert not key_set.snapshot.admits(f"{ENTITY}/pubsub/location_fix/gnss/0")

    def test_new_snapshot_does_not_reuse_old_verdicts(self):
        key_set, _ = make_key_set([f"{ENTITY}/**"])
        log_key = f"{ENTITY}/pubsub/log_message/docker/ntrip"
        assert key_set.snapshot.admits(log_key)

        key_set.set_config({"keys": [f"{ENTITY}/**"], "exclude_keys": [LOGS]})
        assert not key_set.snapshot.admits(log_key)


# =============================================================================
# Live replacement
# =============================================================================


class TestSetConfig:

    def test_starts_at_generation_zero_from_the_cli(self):
        key_set, subs = make_key_set(["a/**", "b/**"], ["a/x"])
        assert key_set.snapshot.generation == 0
        assert key_set.snapshot.source == "cli"
        assert subs.active == {"a/**", "b/**"}
        assert key_set.get_config() == {
            "keys": ["a/**", "b/**"],
            "exclude_keys": ["a/x"],
        }

    def test_replaces_subscriptions_and_bumps_generation(self):
        key_set, subs = make_key_set(["a/**", "b/**"])
        key_set.set_config({"keys": ["b/**", "c/**"], "exclude_keys": ["c/x"]})

        assert subs.active == {"b/**", "c/**"}
        assert key_set.snapshot.generation == 1
        assert key_set.snapshot.source == "set_config"
        assert key_set.get_config() == {
            "keys": ["b/**", "c/**"],
            "exclude_keys": ["c/x"],
        }

    def test_unchanged_document_is_not_a_new_generation(self):
        key_set, _ = make_key_set(["a/**"], ["a/x"])
        before = key_set.snapshot
        key_set.set_config({"keys": ["a/**"], "exclude_keys": ["a/x"]})
        assert key_set.snapshot is before

    def test_rejected_document_changes_nothing(self):
        key_set, subs = make_key_set(["a/**"], ["a/x"])
        before = (key_set.snapshot, set(subs.active))

        with pytest.raises(ValueError, match="not a valid key expression"):
            key_set.set_config({"keys": ["b/**", "c//d"]})

        assert (key_set.snapshot, subs.active) == before

    def test_failed_subscription_rolls_back(self):
        subs = FakeSubscriptions(fail_on={"d/**"})
        key_set, _ = make_key_set(["a/**"], subs=subs)
        before = key_set.snapshot

        with pytest.raises(RuntimeError, match="cannot subscribe"):
            key_set.set_config({"keys": ["b/**", "c/**", "d/**"]})

        # b and c were declared, then undone; a was never dropped.
        assert subs.active == {"a/**"}
        assert key_set.snapshot is before

    def test_locked_recorder_refuses_before_validating(self):
        key_set, subs = make_key_set(["a/**"], reconfigurable=False)
        before = (key_set.snapshot, set(subs.active))

        # An invalid document still gets the lock's answer, not a validation one.
        with pytest.raises(PermissionError, match="--no-runtime-reconfiguration"):
            key_set.set_config({"keys": []})

        assert (key_set.snapshot, subs.active) == before
        assert key_set.get_config() == {"keys": ["a/**"], "exclude_keys": []}

    def test_close_undeclares_everything(self):
        key_set, subs = make_key_set(["a/**", "b/**"])
        key_set.close()
        assert subs.active == set()


# =============================================================================
# The metadata record
# =============================================================================


def _key_set_records(path):
    with open(path, "rb") as f:
        return [
            m.metadata
            for m in make_reader(f).iter_metadata()
            if m.name == keelson2mcap.KEY_SET_METADATA_NAME
        ]


class TestKeySetMetadata:

    def test_metadata_describes_the_snapshot(self):
        key_set, _ = make_key_set(["a/**"], ["a/x"])
        record = key_set.snapshot.metadata()
        assert json.loads(record["keys"]) == ["a/**"]
        assert json.loads(record["exclude_keys"]) == ["a/x"]
        assert record["generation"] == "0"
        assert record["source"] == "cli"
        assert record["changed_at"].endswith("+00:00")
        assert all(isinstance(v, str) for v in record.values())

    def test_every_change_is_recorded_in_the_open_file(self, tmp_path):
        key_set, _ = make_key_set(["a/**"])
        writer = MCAPRotatingWriter(output_folder=tmp_path, file_pattern="rec")
        writer.open()
        writer.set_metadata(
            keelson2mcap.KEY_SET_METADATA_NAME, key_set.snapshot.metadata()
        )
        key_set.set_config({"keys": ["a/**"], "exclude_keys": ["a/x"]})
        writer.set_metadata(
            keelson2mcap.KEY_SET_METADATA_NAME, key_set.snapshot.metadata()
        )
        writer.close()

        records = _key_set_records(tmp_path / "rec.mcap")
        assert [r["generation"] for r in records] == ["0", "1"]
        assert json.loads(records[1]["exclude_keys"]) == ["a/x"]
