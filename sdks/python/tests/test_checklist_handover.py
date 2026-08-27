"""Tests for the watch handover protocol (protocol-specification.md §7.5).

Same job as test_checklist.py next door, for the subject beside it.
`checklist_handover` is a last-writer-wins key with several asymmetric writers —
the offerer offers, the relief answers, the vessel may answer after them — and
the rules that keep that survivable are precedence rules a `.proto` file cannot
state. This file holds the *schema* half of §7.5 to the code: that the fields
those rules operate on exist, that the distinctions §7.5 depends on are
structural rather than inferred from absences, and that the sentinels which make
a rule fail closed stay at zero.

Five tests are behavioural rather than schema, and each is a property that only
shows up at runtime: that an unset reading reports nothing, that a MEASURED zero
survives, that an open item defaults to required, that the floor a verdict was
judged against is distinguishable from an unrecorded one, and that a record
survives an envelope round trip.
"""

import keelson

from keelson import qos
from keelson.payloads.ChecklistEvent_pb2 import ChecklistEvent
from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover
from keelson.payloads.ChecklistState_pb2 import ChecklistState
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority


def _field_names(message_class):
    return {field.name for field in message_class.DESCRIPTOR.fields}


def test_the_subject_resolves_to_its_type():
    assert keelson.is_subject_well_known("checklist_handover")
    assert (
        keelson.get_subject_schema("checklist_handover") == "keelson.ChecklistHandover"
    )


def test_the_handover_id_is_a_single_string_token():
    """§7.5's key is `checklist_handover/{handover_id}`, matched by one wildcard.

    A composite id publishes without error and never persists — the storage's
    key expression simply does not match it. Same failure §7.3 records for
    run_id, procedure_id and evidence_id.
    """
    field = ChecklistHandover.DESCRIPTOR.fields_by_name["handover_id"]
    assert field.type == field.TYPE_STRING


def test_the_status_ladder_is_six_plus_a_sentinel():
    assert ChecklistHandover.HandoverStatus.keys() == [
        "HANDOVER_STATUS_UNKNOWN",
        "HANDOVER_STATUS_OFFERED",
        "HANDOVER_STATUS_PENDING_VESSEL",
        "HANDOVER_STATUS_ACCEPTED",
        "HANDOVER_STATUS_REFUSED",
        "HANDOVER_STATUS_CANCELLED",
        "HANDOVER_STATUS_EXPIRED",
    ]
    assert ChecklistHandover.HANDOVER_STATUS_UNKNOWN == 0


def test_the_terminal_numbering_agrees_with_the_precedence_table():
    """A mnemonic, NOT the rule. §7.5.1's table is what is normative.

    The numbering is laid out to agree with `accepted > refused > cancelled >
    expired` so a reader scanning the enum is not misled. A future terminal
    status will be appended at the end whatever its precedence — renumbering
    would silently reinterpret every record already in storage — so this test
    pins today's agreement rather than forbidding tomorrow's append.
    """
    assert (
        ChecklistHandover.HANDOVER_STATUS_ACCEPTED
        < ChecklistHandover.HANDOVER_STATUS_REFUSED
        < ChecklistHandover.HANDOVER_STATUS_CANCELLED
        < ChecklistHandover.HANDOVER_STATUS_EXPIRED
    )


def test_the_live_statuses_are_the_two_that_are_not_absorbing():
    """OFFERED waits on the relief, PENDING_VESSEL on the vessel."""
    assert (
        ChecklistHandover.HANDOVER_STATUS_OFFERED
        < ChecklistHandover.HANDOVER_STATUS_ACCEPTED
    )
    assert (
        ChecklistHandover.HANDOVER_STATUS_PENDING_VESSEL
        < ChecklistHandover.HANDOVER_STATUS_ACCEPTED
    )


def test_a_reading_encodes_absence_twice():
    """§7.5.5. Presence says whether there is a number; availability says why not.

    Neither half is redundant: a oneof cannot express the difference between
    "no GNSS fitted" and "the GNSS failed", and an availability enum on a bare
    double cannot distinguish a measured zero from a defaulted one.
    """
    assert "value" in {
        oneof.name for oneof in ChecklistHandover.Reading.DESCRIPTOR.oneofs
    }
    assert ChecklistHandover.AVAILABILITY_UNKNOWN == 0
    assert ChecklistHandover.Availability.keys() == [
        "AVAILABILITY_UNKNOWN",
        "AVAILABILITY_NO_SOURCE",
        "AVAILABILITY_AVAILABLE",
        "AVAILABILITY_STALE",
        "AVAILABILITY_INVALID",
    ]


def test_an_unset_reading_reports_no_value_and_no_availability():
    """Behavioural. The default must fail CLOSED under §7.5.5's MUST."""
    reading = ChecklistHandover.Reading()
    assert reading.WhichOneof("value") is None
    assert reading.availability == ChecklistHandover.AVAILABILITY_UNKNOWN


def test_a_measured_zero_survives_a_round_trip():
    """Behavioural, and the case the oneof exists for.

    Stopped in the water is 0.0 kn and it is a real reading. A nullable double
    would lose it; explicit presence keeps it.
    """
    reading = ChecklistHandover.Reading(
        availability=ChecklistHandover.AVAILABILITY_AVAILABLE, scalar=0.0
    )
    decoded = ChecklistHandover.Reading.FromString(reading.SerializeToString())

    assert decoded.WhichOneof("value") == "scalar"
    assert decoded.scalar == 0.0
    assert decoded.availability == ChecklistHandover.AVAILABILITY_AVAILABLE


def test_a_position_is_a_coordinate_in_the_same_oneof():
    """So a position cannot claim availability differently from how it claims a value."""
    field = ChecklistHandover.Reading.DESCRIPTOR.fields_by_name["coordinate"]
    assert field.message_type.full_name == "keelson.Coordinate"
    assert field.containing_oneof is not None
    assert field.containing_oneof.name == "value"


def test_expiry_lives_on_the_record():
    """§7.5.4, and deliberately unlike the lease rule in §6.5.1.

    A lease arms on the receiver's clock because an early expiry lets two
    stations act at once. A handover offer interlocks nothing and outlives every
    tab, so the deadline has to be readable by a station that booted after it.
    """
    field = ChecklistHandover.DESCRIPTOR.fields_by_name["expires_at"]
    assert field.message_type.full_name == "google.protobuf.Timestamp"


def test_the_reliefs_signature_and_the_vessels_answer_are_two_instants():
    """§7.5.2 forbids one overwriting the other, so both fields must exist."""
    names = _field_names(ChecklistHandover)
    assert "accepted_at" in names
    assert "vessel_confirmed_at" in names


def test_a_vessel_refusal_is_stated_not_inferred():
    """§7.5.2. The JSON form this replaces derived it from two absences.

    A vessel timestamp set while the refusing party was unset is an absence, and
    an absence reads as "unknown" rather than as "the vessel". Two clients had
    each derived it inline; a third would have had to guess.
    """
    assert ChecklistHandover.RefusalSource.keys() == [
        "REFUSAL_SOURCE_UNKNOWN",
        "REFUSAL_SOURCE_OPERATOR",
        "REFUSAL_SOURCE_VESSEL",
    ]
    assert ChecklistHandover.REFUSAL_SOURCE_UNKNOWN == 0
    assert "refusal_source" in _field_names(ChecklistHandover)


def test_the_verdict_is_typed_rather_than_prose():
    """OperationalAuthority.proto forbids parsing its own reason; so does this.

    Everything the prose says is available structurally, which is exactly why
    the prose may not be parsed. An opaque verdict would make the one field the
    feature exists to audit unreadable to anything but its writer.
    """
    assert ChecklistHandover.VesselVerdict.Gate.keys() == [
        "GATE_UNKNOWN",
        "GATE_CONFIRMED",
        "GATE_NON_AUTHORIZING",
        "GATE_BELOW_FLOOR",
        "GATE_NO_AUTHORITY",
        "GATE_STALE_AUTHORITY",
    ]
    reason = ChecklistHandover.VesselVerdict.DESCRIPTOR.fields_by_name["reason"]
    assert reason.type == reason.TYPE_STRING


def test_an_unrecorded_gate_stays_countable():
    """A refusal whose cause was never recorded is its own bucket, not a guess.

    Verdicts written by a producer predating the gate field carry nothing, and
    folding them into either reading would corrupt the only population anyone
    counts to decide where the configured floor belongs.
    """
    assert ChecklistHandover.VesselVerdict.GATE_UNKNOWN == 0


def test_the_floor_the_verdict_was_judged_against_has_explicit_presence():
    """Behavioural. Without it, 0 reads as an AUTHORITY_LEVEL_UNKNOWN floor.

    Re-judging a stored refusal against a different setting is the only question
    anyone asks of these records afterwards, and it needs "never recorded" to be
    distinguishable from "recorded as zero".
    """
    verdict = ChecklistHandover.VesselVerdict()
    assert verdict.HasField("min_level") is False

    verdict.min_level = OperationalAuthority.AUTHORITY_LEVEL_UNKNOWN
    assert verdict.HasField("min_level") is True


def test_the_verdict_reuses_the_authority_types():
    """Reused rather than paralleled, so a Cause added upstream needs no change here."""
    fields = ChecklistHandover.VesselVerdict.DESCRIPTOR.fields_by_name
    assert (
        fields["level"].enum_type.full_name
        == "keelson.OperationalAuthority.AuthorityLevel"
    )
    assert (
        fields["min_level"].enum_type.full_name
        == "keelson.OperationalAuthority.AuthorityLevel"
    )
    assert (
        fields["constraints"].message_type.full_name
        == "keelson.OperationalAuthority.AuthorityConstraint"
    )


def test_an_open_item_defaults_to_required():
    """Behavioural, and the opposite default from ChecklistProcedure.Item.

    There `is_required` is a plain bool defaulting to false. Here a publisher
    that did not say must not thereby understate what is outstanding at a watch
    change, so unset MUST be read as required.
    """
    item = ChecklistHandover.OpenItem()
    assert item.HasField("is_required") is False

    item.is_required = False
    assert item.HasField("is_required") is True


def test_an_open_item_reuses_the_run_item_status():
    """A parallel enum would need a mapping table in every client, and would drift."""
    field = ChecklistHandover.OpenItem.DESCRIPTOR.fields_by_name["status"]
    assert (
        field.enum_type.full_name == "keelson.ChecklistState.ItemState.ItemStatus"
    )


def test_risks_are_one_ordered_list_with_a_kind():
    """§7.5.2 freezes the list; the ORDER in it is the operator's judgement.

    Two repeated fields would destroy that ordering and make every client invent
    its own interleave rule for a safety briefing.
    """
    field = ChecklistHandover.DESCRIPTOR.fields_by_name["active_risks"]
    assert field.is_repeated
    assert field.message_type.full_name == "keelson.ChecklistHandover.ActiveRisk"
    assert ChecklistHandover.ActiveRisk.RISK_KIND_UNKNOWN == 0

    names = _field_names(ChecklistHandover)
    assert "flagged_items" not in names
    assert "active_alerts" not in names


def test_risk_severity_reuses_the_shared_scale():
    """Severity.proto asks to be the single source of truth for the whole bus."""
    field = ChecklistHandover.ActiveRisk.DESCRIPTOR.fields_by_name["severity"]
    assert field.enum_type.full_name == "keelson.SeverityLevel"


def test_the_run_link_is_one_directional():
    """A run exists whether or not a watch changed; the handover references it.

    Both subjects are durable, so a consumer derives the reverse link locally by
    matching run_id. This test is what stops someone helpfully adding a handover
    id to the checklist messages and creating a second writer to the run's key.
    """
    assert "run_id" in _field_names(ChecklistHandover)
    assert "handover_id" not in _field_names(ChecklistState)
    assert "handover_id" not in _field_names(ChecklistEvent)


def test_a_handover_does_not_carry_a_command_lease():
    """The briefing is not the conn — command_authority moves that, separately.

    Accepting a briefing and being ready to take control are two decisions, and
    a record that carried lease machinery would invite a consumer to conflate
    them.
    """
    names = _field_names(ChecklistHandover)
    assert "token" not in names
    assert "lease_ttl_seconds" not in names
    assert "heartbeat_interval_seconds" not in names
    assert "released" not in names


def test_the_stance_is_assigned_rather_than_inherited():
    """`elevated` buys latency, not delivery — §7.3's storage is the backfill.

    Every profile in qos.yaml is congestion_control: DROP, so the stance says
    an offer should reach the relief's screen promptly, and says nothing about
    whether it arrives. A shed publish is listed in §7.4 as unsolved.
    """
    assert qos.profile_name_for("checklist_handover") == "elevated"
    assert qos.qos_for("checklist_handover").congestion_control == "DROP"


def test_a_handover_survives_an_envelope_round_trip():
    """Behavioural. The whole record, through the wire, with an absence in it."""
    handover = ChecklistHandover(
        handover_id="hv_1",
        run_id="run_7",
        status=ChecklistHandover.HANDOVER_STATUS_PENDING_VESSEL,
        offered_by=ChecklistHandover.Party(operator_id="ted", roc_site="roc_a"),
        vessel=ChecklistHandover.VesselRef(realm="rise", entity_id="sf18"),
        vessel_status=ChecklistHandover.VesselStatus(
            platform_name="SF18",
            heading_deg=ChecklistHandover.Reading(
                availability=ChecklistHandover.AVAILABILITY_NO_SOURCE
            ),
        ),
        vessel_verdict=ChecklistHandover.VesselVerdict(
            gate=ChecklistHandover.VesselVerdict.GATE_BELOW_FLOOR,
            level=OperationalAuthority.AUTHORITY_LEVEL_SUPERVISED_REMOTE,
            min_level=OperationalAuthority.AUTHORITY_LEVEL_REMOTE_CONTROLLED,
        ),
    )
    handover.expires_at.FromSeconds(1_760_000_900)

    envelope = keelson.enclose(handover.SerializeToString())
    _received_at, _enclosed_at, payload = keelson.uncover(envelope)
    decoded = ChecklistHandover.FromString(payload)

    assert decoded.status == ChecklistHandover.HANDOVER_STATUS_PENDING_VESSEL
    assert decoded.expires_at.seconds == 1_760_000_900

    # The absence survived as an absence, not as a zero.
    assert (
        decoded.vessel_status.heading_deg.availability
        == ChecklistHandover.AVAILABILITY_NO_SOURCE
    )
    assert decoded.vessel_status.heading_deg.WhichOneof("value") is None

    # And the floor came back distinguishable from never-recorded.
    assert decoded.vessel_verdict.HasField("min_level")
    assert (
        decoded.vessel_verdict.min_level
        == OperationalAuthority.AUTHORITY_LEVEL_REMOTE_CONTROLLED
    )
    assert decoded.vessel_verdict.gate == ChecklistHandover.VesselVerdict.GATE_BELOW_FLOOR
