"""Tests for the collaborative checklist protocol (protocol-specification.md §7).

§7 exists because `checklist_state` is a last-writer-wins key with more than one
writer, and what keeps that survivable is a set of merge rules that a `.proto`
file cannot state on its own. This file holds the *schema* half of §7 to the
code: that the fields those rules operate on exist and carry the types the rules
assume, that the compatibility promises the comments make are real, and that the
numbers deliberately withheld stay withheld.

It does not exercise a reducer — there is no reference implementation in this
repo yet, by decision (§7.4). What it does do is fail loudly if a future edit
removes the ground the rules stand on.
"""

import keelson

from google.protobuf import descriptor_pb2
from keelson import qos
from keelson.payloads.ChecklistEvent_pb2 import ChecklistEvent
from keelson.payloads.ChecklistEvidence_pb2 import ChecklistItemEvidence
from keelson.payloads.ChecklistPresence_pb2 import ChecklistPresence
from keelson.payloads.ChecklistProcedure_pb2 import ChecklistProcedure
from keelson.payloads.ChecklistState_pb2 import ChecklistState


CHECKLIST_SUBJECTS = {
    "checklist_event": "keelson.ChecklistEvent",
    "checklist_state": "keelson.ChecklistState",
    "checklist_presence": "keelson.ChecklistPresence",
    "checklist_procedure": "keelson.ChecklistProcedure",
    "checklist_evidence": "foxglove.CompressedImage",
}


def _field_names(message_class):
    return {field.name for field in message_class.DESCRIPTOR.fields}


def test_checklist_subjects_resolve_to_their_types():
    for subject, type_name in CHECKLIST_SUBJECTS.items():
        assert keelson.is_subject_well_known(subject)
        assert keelson.get_subject_schema(subject) == type_name


def test_evidence_metadata_is_not_a_subject():
    """ChecklistItemEvidence travels embedded, never on a key of its own — §7.3.

    `checklist_evidence` carries the image BYTES. Publishing the metadata under
    the same subject would put two different types on one key expression, and
    the storage behind it would keep whichever arrived last.
    """
    assert not keelson.is_subject_well_known("checklist_item_evidence")
    assert (
        keelson.get_subject_schema("checklist_evidence") == "foxglove.CompressedImage"
    )


def test_the_run_is_the_unit_of_state_not_the_procedure():
    """§7.3 keys `checklist_state` by run_id, so all three carry one."""
    assert "run_id" in _field_names(ChecklistState)
    assert "run_id" in _field_names(ChecklistEvent)
    assert "active_run_id" in _field_names(ChecklistPresence)


def test_every_id_in_the_checklist_family_is_a_single_string_token():
    """§7.3: each id is one key token, so `.../{subject}/*` covers it.

    A non-string id would be formatted into the key by each client's own
    conventions, and a composite one publishes without error and never
    persists — the storage's key expression simply does not match it.
    """
    for message_class, field_name in (
        (ChecklistState, "run_id"),
        (ChecklistState, "procedure_id"),
        (ChecklistEvent, "event_id"),
        (ChecklistEvent, "run_id"),
        (ChecklistItemEvidence, "evidence_id"),
        (ChecklistProcedure, "procedure_id"),
    ):
        field = message_class.DESCRIPTOR.fields_by_name[field_name]
        assert field.type == field.TYPE_STRING, f"{message_class.__name__}.{field_name}"


def test_the_merged_collections_are_keyed_so_a_union_is_possible():
    """§7.2 unions `evidence` and `notes` by id rather than replacing them.

    A union needs a key. Repeated fields whose elements carry no identity can
    only be replaced, which is the merge §7.2 forbids.
    """
    assert "evidence_id" in _field_names(ChecklistItemEvidence)
    assert "note_id" in _field_names(ChecklistState.ItemNote)

    evidence = ChecklistState.ItemState.DESCRIPTOR.fields_by_name["evidence"]
    notes = ChecklistState.ItemState.DESCRIPTOR.fields_by_name["notes"]
    assert evidence.is_repeated
    assert notes.is_repeated


def test_a_flag_can_be_dated():
    """§7.2 merges by value, so a flag with no timestamp cannot be ordered.

    The four other flag fields say an item is flagged, why, and by whom. Without
    `flagged_at` a bootstrapped flag renders as an undated row beside rows that
    carry real times.
    """
    assert "flagged_at" in _field_names(ChecklistState.ItemState)


def test_a_run_says_who_created_it_and_where():
    """Authorship is carried, not inferred from whoever last republished.

    `published_by_operator` / `published_by_site` name whoever ticked the 30 s
    snapshot timer. Substituting them for authorship is wrong in a way that is
    hard to see, and Crowsnest did exactly that: the author flipped to the last
    republisher on every tick, and because the same field decided which station
    republishes a run and announces its end, the publish duty migrated with it.
    """
    names = _field_names(ChecklistState)
    assert "created_by" in names
    assert "created_by_site" in names

    # Distinct fields, not aliases — the whole point is that they differ.
    assert "published_by_operator" in names
    assert "published_by_site" in names


def test_a_planned_run_can_be_dated_without_having_started():
    """A PLANNED run has no `started_at` by definition.

    Without `created_at` a receiver falls back to the snapshot's own timestamp —
    the publish tick — so a run queued last watch sorts as if it were created
    moments ago, and the planned board orders by the wrong thing.
    """
    field = ChecklistState.DESCRIPTOR.fields_by_name["created_at"]
    assert field.message_type.full_name == "google.protobuf.Timestamp"


def test_authorship_says_site_rather_than_roc_site():
    """A site running a watch is not necessarily a ROC.

    Six authorship fields carry a site, and they agree on the spelling. The bare
    `roc_site` identity fields on ChecklistEvent and ChecklistPresence are NOT
    part of this: they name the source of a message, which the key expression
    already establishes, rather than attributing an act.
    """
    assert "created_by_site" in _field_names(ChecklistState)
    assert "published_by_site" in _field_names(ChecklistState)
    item = _field_names(ChecklistState.ItemState)
    assert {"started_by_site", "completed_by_site", "flagged_by_site"} <= item
    assert "author_site" in _field_names(ChecklistState.ItemNote)
    assert "author_site" in _field_names(ChecklistItemEvidence)

    # The old spelling is gone everywhere it was an authorship pair.
    for names in (
        _field_names(ChecklistState),
        item,
        _field_names(ChecklistState.ItemNote),
        _field_names(ChecklistItemEvidence),
    ):
        assert not any(n.endswith("_roc_site") for n in names)


def test_an_abandoned_run_can_carry_its_reason():
    """§7.3: `checklist_event` is not persisted, so a reason living only in the
    announcing event is lost to anyone who bootstraps from the snapshot."""
    assert "abandon_reason" in _field_names(ChecklistState)


def test_terminal_run_statuses_exist_to_be_absorbing():
    """§7.2 makes COMPLETED and ABANDONED beat any non-terminal status."""
    names = set(ChecklistState.RunStatus.keys())
    assert {
        "RUN_STATUS_UNKNOWN",
        "RUN_STATUS_PLANNED",
        "RUN_STATUS_ACTIVE",
        "RUN_STATUS_COMPLETED",
        "RUN_STATUS_ABANDONED",
    } == names


def test_item_status_is_a_monotone_ladder_with_no_unknown():
    """§7.2 moves an item PENDING -> IN_PROGRESS -> COMPLETED and never back.

    Deliberately the one enum in this family without an UNKNOWN = 0: an item has
    no unknown state, it has simply not been started. Pinned because adding one
    later would renumber every value and silently reinterpret every recording.
    """
    status = ChecklistState.ItemState.ItemStatus
    assert status.keys() == [
        "ITEM_STATUS_PENDING",
        "ITEM_STATUS_IN_PROGRESS",
        "ITEM_STATUS_COMPLETED",
    ]
    assert status.values() == [0, 1, 2]


def test_the_archive_field_is_only_useful_if_it_carries_the_item_text():
    """§7.2 re-emits `items_snapshot` so a completed run renders against the
    wording it was actually worked against, not today's template."""
    field = ChecklistState.DESCRIPTOR.fields_by_name["items_snapshot"]
    assert field.is_repeated
    assert field.message_type.full_name == "keelson.ChecklistProcedure.Item"


def test_sub_tasks_are_a_parent_pointer_not_nested_items():
    """A pointer leaves ItemState and ChecklistEvent untouched, so a sub-task
    syncs, is evidenced and is audited exactly like any other item."""
    names = _field_names(ChecklistProcedure.Item)
    assert "parent_item_id" in names
    assert "children" not in names
    assert "items" not in names


def test_reverted_still_decodes_and_is_not_reserved():
    """EVENT_TYPE_ITEM_REVERTED = 4 is the old spelling of ITEM_REOPENED = 15.

    Recordings and durable snapshots already contain 4. Reserving it would make
    every one of them fail to decode, which is precisely the cost the rename was
    designed to avoid.
    """
    assert ChecklistEvent.EventType.Value("EVENT_TYPE_ITEM_REVERTED") == 4
    assert ChecklistEvent.EventType.Value("EVENT_TYPE_ITEM_REOPENED") == 15

    reverted = ChecklistEvent(
        event_id="evt-1", event_type=ChecklistEvent.EVENT_TYPE_ITEM_REVERTED
    )
    assert ChecklistEvent.FromString(reverted.SerializeToString()).event_type == 4


def test_evidence_retraction_stays_reserved():
    """§7.4: 14 is claimed for a retraction that needs design, not a number."""
    proto = descriptor_pb2.EnumDescriptorProto()
    ChecklistEvent.EventType.DESCRIPTOR.CopyToProto(proto)

    ranges = [(r.start, r.end) for r in proto.reserved_range]
    assert any(start <= 14 <= end for start, end in ranges), ranges
    assert 14 not in ChecklistEvent.EventType.values()


def test_a_corrected_time_is_a_timestamp_and_its_target_is_an_enum():
    """§7.2 compares timestamps as values, which an ISO-8601 string is not.

    `EVENT_TYPE_TIME_SET` used to carry the instant as a string in `detail` and
    name its target by matching `reference_id` against a spelling. Every
    consumer wrote its own parser, and a typo was a silent no-op rather than an
    error.
    """
    corrected_time = ChecklistEvent.DESCRIPTOR.fields_by_name["corrected_time"]
    assert corrected_time.message_type.full_name == "google.protobuf.Timestamp"

    assert ChecklistEvent.TimeField.keys() == [
        "TIME_FIELD_UNKNOWN",
        "TIME_FIELD_ITEM_STARTED_AT",
        "TIME_FIELD_ITEM_COMPLETED_AT",
        "TIME_FIELD_RUN_STARTED_AT",
    ]

    # The legacy pair stays, so a publisher can write both during the transition
    # and a recording of the string form keeps replaying.
    assert {"detail", "reference_id"} <= _field_names(ChecklistEvent)


def test_a_time_correction_survives_an_envelope_round_trip():
    event = ChecklistEvent(
        event_id="evt-42",
        event_type=ChecklistEvent.EVENT_TYPE_TIME_SET,
        run_id="run-7",
        item_id="item-3",
        corrected_field=ChecklistEvent.TIME_FIELD_ITEM_COMPLETED_AT,
    )
    event.corrected_time.FromSeconds(1_760_000_000)

    envelope = keelson.enclose(event.SerializeToString())
    _received_at, _enclosed_at, payload = keelson.uncover(envelope)
    decoded = ChecklistEvent.FromString(payload)

    assert decoded.corrected_time.seconds == 1_760_000_000
    assert decoded.corrected_field == ChecklistEvent.TIME_FIELD_ITEM_COMPLETED_AT
    assert decoded.event_type == ChecklistEvent.EVENT_TYPE_TIME_SET


def test_cursor_state_carries_its_own_enum_name():
    """Every other enum in this family is prefixed with its type name."""
    assert ChecklistPresence.CursorState.keys() == [
        "CURSOR_STATE_IDLE",
        "CURSOR_STATE_VIEWING",
        "CURSOR_STATE_EDITING_NOTE",
    ]


def test_presence_carries_every_open_run_not_just_the_focused_one():
    """A single scalar makes an operator's other open runs invisible to peers."""
    field = ChecklistPresence.DESCRIPTOR.fields_by_name["open_run_ids"]
    assert field.is_repeated
    assert field.type == field.TYPE_STRING


def test_the_four_stances_are_assigned_rather_than_inherited():
    """§7 gives a heartbeat, a live stream and a durable snapshot different QoS.

    Three of the five are assigned; the other two are `default` by decision, and
    qos.yaml records why. Pinned so a future edit cannot quietly flatten them.
    """
    assert qos.profile_name_for("checklist_presence") == "transient"
    assert qos.profile_name_for("checklist_event") == "elevated"
    assert qos.profile_name_for("checklist_state") == "background"

    # Evidence carries the same foxglove.CompressedImage as image_compressed,
    # which is `transient`. The stance differs because a camera frame is
    # corrected by the next one and an evidence photo is never republished, so
    # retransmitting a lost fragment is worth paying for here and not there.
    #
    # Both halves are asserted deliberately. RELIABLE alone reads as "this
    # cannot be lost", which is not what any profile in the file provides — so
    # pin the congestion stance beside it. A shed publish is unrecoverable and
    # is listed in protocol-specification.md §7.4 as unsolved.
    assert qos.profile_name_for("checklist_evidence") == "default"
    assert qos.qos_for("checklist_evidence").reliability == "RELIABLE"
    assert qos.qos_for("checklist_evidence").congestion_control == "DROP"
    assert qos.profile_name_for("image_compressed") == "transient"

    assert qos.profile_name_for("checklist_procedure") == "default"
