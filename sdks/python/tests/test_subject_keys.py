"""subject_keys.yaml — the key shape and storage of every subject (keelson #319)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

import keelson
from keelson import subject_keys

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
SUBJECT_KEYS = yaml.safe_load((REPO / "messages" / "subject_keys.yaml").read_text())
SUBJECTS = yaml.safe_load((REPO / "messages" / "subjects.yaml").read_text())


def test_every_listed_subject_is_well_known():
    unknown = sorted(set(SUBJECT_KEYS["subjects"]) - set(SUBJECTS))
    assert (
        not unknown
    ), f"subject_keys.yaml lists subjects not in subjects.yaml: {unknown}"


def test_the_default_is_a_producer_stream_that_is_not_stored():
    assert subject_keys.get_subject_keys("location_fix") == [
        keelson.SubjectKey("{producer}", "stream", "none")
    ]


def test_every_row_uses_the_vocabulary():
    for subject, rows in SUBJECT_KEYS["subjects"].items():
        assert rows, subject
        for row in rows:
            assert set(row) == {"key", "shape", "storage"}, (subject, row)
            assert row["shape"] in subject_keys.SHAPES, (subject, row)
            assert row["storage"] in subject_keys.STORAGES, (subject, row)


def test_a_stream_or_instance_key_carries_a_producer_unless_as_built():
    # §2.1.1: a stream is keyed by its producer, a slot never is. The as-built
    # §6-§7 instance keys predate the rule and carry no producer (§6.3).
    for subject, rows in SUBJECT_KEYS["subjects"].items():
        for row in rows:
            if row["shape"] == "slot":
                assert "{producer}" not in row["key"], (subject, row)


def _protocol_rows():
    sys.path.insert(0, str(REPO / "scripts"))
    from protocols_lib import load_all  # noqa: E402

    for protocol in load_all(REPO / "protocols"):
        for row in protocol.data["keys"]:
            yield protocol.name, row


def test_every_protocol_key_row_is_here_verbatim():
    rows = list(_protocol_rows())
    assert rows
    for name, row in rows:
        listed = SUBJECT_KEYS["subjects"].get(row["subject"])
        assert listed, f"{name}: {row['subject']} is not in subject_keys.yaml"
        wanted = {k: row[k] for k in ("key", "shape", "storage")}
        assert wanted in listed, (name, row["subject"], wanted, listed)


@pytest.mark.parametrize(
    "subject",
    [
        "advice_disposition",
        "navigation_advice",
        "encounter",
        "route",
        "voyage",
        "route_change_event",
        "checklist_state",
        "checklist_procedure",
        "checklist_evidence",
        "checklist_handover",
    ],
)
def test_crowsnest_record_subjects_are_records(subject):
    # The keelson-side half of crowsnest's hard-coded RECORD_SUBJECTS, which
    # this file replaces.
    assert keelson.is_record_subject(subject)
    assert keelson.is_stored_subject(subject)


@pytest.mark.parametrize(
    "subject",
    ["location_fix", "navigation_state", "checklist_event", "checklist_presence"],
)
def test_streams_are_not_records(subject):
    assert not keelson.is_record_subject(subject)


def test_a_subject_can_have_several_key_shapes():
    rows = keelson.get_subject_keys("route")
    assert [(r.key, r.shape, r.storage) for r in rows] == [
        ("{route_id}/edition/{route_edition_number}", "instance", "latest"),
        ("{route_id}/latest", "slot", "latest"),
    ]


def test_stored_is_independent_of_shape():
    # A stored slot and an unstored one; a stored stream.
    assert keelson.is_stored_subject("advice_disposition")
    assert not keelson.is_stored_subject("route_status")
    assert keelson.is_record_subject("route_status")
    assert keelson.is_stored_subject("scenario_event")
    assert not keelson.is_record_subject("scenario_event")


def test_unknown_vocabulary_is_refused(tmp_path):
    bad = tmp_path / "subject_keys.yaml"
    bad.write_text(
        "subjects:\n  x:\n    - {key: '{producer}', shape: stream, storage: forever}\n"
    )
    with pytest.raises(ValueError):
        subject_keys.add_subject_keys(bad)
