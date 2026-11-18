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
    from build_subject_keys import protocol_rows  # noqa: E402

    return protocol_rows(REPO / "protocols")


def test_a_protocol_is_the_source_for_its_subjects():
    """The SDK answers for a protocol's subject with that protocol's rows, so a
    protocol added on any branch never has to repeat itself here."""
    rows = _protocol_rows()
    assert rows
    for subject, wanted in rows.items():
        got = [
            {"key": r.key, "shape": r.shape, "storage": r.storage}
            for r in keelson.get_subject_keys(subject)
        ]
        assert got == wanted, (subject, got, wanted)


def test_every_other_listed_subject_comes_from_subject_keys_yaml():
    protocol = _protocol_rows()
    for subject, wanted in SUBJECT_KEYS["subjects"].items():
        if subject in protocol:
            continue
        got = [
            {"key": r.key, "shape": r.shape, "storage": r.storage}
            for r in keelson.get_subject_keys(subject)
        ]
        assert got == wanted, (subject, got, wanted)


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
    # Subjects no protocol covers, so the default stream answers for them.
    ["location_fix", "heading_true_north_deg"],
)
def test_streams_are_not_records(subject):
    assert not keelson.is_record_subject(subject)


def test_a_subject_can_have_several_key_shapes():
    rows = keelson.get_subject_keys("route")
    assert [(r.key, r.shape, r.storage) for r in rows] == [
        ("{route_id}/edition/{route_edition_number}", "instance", "latest"),
        ("{route_id}/latest", "slot", "latest"),
    ]


def test_stored_is_independent_of_shape(tmp_path):
    # A stored slot and an unstored one; a stored stream. Synthetic subjects,
    # so the rule is tested and not which protocol currently stores what.
    extra = tmp_path / "subject_keys.yaml"
    extra.write_text(
        "subjects:\n"
        "  zz_stored_slot:\n    - {key: '{thing_id}', shape: slot, storage: latest}\n"
        "  zz_unstored_slot:\n    - {key: '{thing_id}', shape: slot, storage: none}\n"
        "  zz_stored_stream:\n    - {key: '{producer}', shape: stream, storage: latest}\n"
    )
    subject_keys.add_subject_keys(extra)
    assert keelson.is_stored_subject("zz_stored_slot")
    assert not keelson.is_stored_subject("zz_unstored_slot")
    assert keelson.is_record_subject("zz_unstored_slot")
    assert keelson.is_stored_subject("zz_stored_stream")
    assert not keelson.is_record_subject("zz_stored_stream")


def test_unknown_vocabulary_is_refused(tmp_path):
    bad = tmp_path / "subject_keys.yaml"
    bad.write_text(
        "subjects:\n  x:\n    - {key: '{producer}', shape: stream, storage: forever}\n"
    )
    with pytest.raises(ValueError):
        subject_keys.add_subject_keys(bad)
