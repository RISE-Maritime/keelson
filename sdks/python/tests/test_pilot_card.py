"""Tests for the pilot card (protocol-specification.md §7.6, keelson#312).

Same job as test_checklist_handover.py next door, for the record beside it.
`pilot_card` is a last-writer-wins key with several writers — the station
drafting it, the one where the master signs, the one where the pilot
acknowledges — and the rules that keep that survivable are precedence rules a
`.proto` file cannot state. This file holds the *schema* half of §7.6 to the
code: that the fields those rules operate on exist, that the distinctions §7.6
depends on are structural rather than inferred from absences, and that the
sentinels which make a rule fail closed stay at zero.

Three tests are behavioural rather than schema, and each is a property that
only shows up at runtime: that an unset figure reports nothing, that a figure
SET to zero survives, and that a signed card survives an envelope round trip
with an absence in it.
"""

import keelson
from keelson import qos
from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover
from keelson.payloads.PilotCard_pb2 import PilotCard


def _field_names(message_class):
    return {field.name for field in message_class.DESCRIPTOR.fields}


BODY_FIELDS = {
    "particulars",
    "drafts",
    "machinery",
    "speed_table",
    "equipment",
    "defects",
    "other_information",
}


def test_the_subject_resolves_to_its_type():
    assert keelson.is_subject_well_known("pilot_card")
    assert keelson.get_subject_schema("pilot_card") == "keelson.PilotCard"


def test_the_card_id_is_a_single_string_token():
    """§7.6's key is `pilot_card/{card_id}`, matched by one wildcard.

    A composite id publishes without error and never persists — the storage's
    key expression simply does not match it. Same failure §7.3 records for
    run_id, procedure_id, evidence_id and handover_id.
    """
    field = PilotCard.DESCRIPTOR.fields_by_name["card_id"]
    assert field.type == field.TYPE_STRING


def test_the_status_ladder_is_three_plus_a_sentinel():
    assert PilotCard.CardStatus.keys() == [
        "CARD_STATUS_UNKNOWN",
        "CARD_STATUS_DRAFT",
        "CARD_STATUS_SIGNED",
        "CARD_STATUS_VOID",
    ]
    assert PilotCard.CARD_STATUS_UNKNOWN == 0


def test_the_terminal_numbering_agrees_with_the_precedence_table():
    """A mnemonic, NOT the rule. §7.6.1's table is what is normative.

    Laid out as the handover's is — the stronger terminal has the lower
    number — so a reader scanning the enum is not misled. A future state will
    be appended at the end whatever its precedence, so this pins today's
    agreement rather than forbidding tomorrow's append.
    """
    assert PilotCard.CARD_STATUS_SIGNED < PilotCard.CARD_STATUS_VOID
    assert PilotCard.CARD_STATUS_DRAFT < PilotCard.CARD_STATUS_SIGNED


def test_the_body_is_the_seven_fields_the_freeze_covers():
    """§7.6.2 names the body field by field; a body field this test does not
    know is one the freeze silently does not cover."""
    assert BODY_FIELDS <= _field_names(PilotCard)


def test_an_unanswered_equipment_line_is_the_zero_value():
    """Not "ready by default": an item nobody looked at is exactly what a pilot
    needs to know about, and a card with one must not be signable."""
    assert PilotCard.EquipmentStatus.keys() == [
        "EQUIPMENT_STATUS_UNKNOWN",
        "EQUIPMENT_STATUS_READY",
        "EQUIPMENT_STATUS_DEFECT",
        "EQUIPMENT_STATUS_NOT_FITTED",
    ]
    assert PilotCard.EquipmentItem().status == PilotCard.EQUIPMENT_STATUS_UNKNOWN


def test_a_defect_names_the_equipment_line_it_is_about():
    """So "marked defective with no defect entry" is checkable, not implied."""
    fields = PilotCard.Defect.DESCRIPTOR.fields_by_name
    assert fields["equipment_id"].type == fields["equipment_id"].TYPE_STRING
    assert "operational_effect" in fields


def test_a_figure_carries_its_value_in_a_oneof():
    """§7.6.5. Unset must be distinguishable from 0 and from ""."""
    assert "value" in {oneof.name for oneof in PilotCard.Figure.DESCRIPTOR.oneofs}
    fields = PilotCard.Figure.DESCRIPTOR.fields_by_name
    assert fields["number"].containing_oneof.name == "value"
    assert fields["text"].containing_oneof.name == "value"


def test_an_unset_figure_reports_no_value():
    """Behavioural. A registry row with no MMSI spells it as 0; the seeded
    figure must be an absence, never a 0 with the master's signature under it."""
    figure = PilotCard.Figure()
    assert figure.WhichOneof("value") is None
    assert figure.source == PilotCard.FIGURE_SOURCE_UNKNOWN


def test_a_figure_set_to_zero_survives_a_round_trip():
    """Behavioural, and the case the oneof exists for."""
    figure = PilotCard.Figure(source=PilotCard.FIGURE_SOURCE_TYPED, number=0.0)
    decoded = PilotCard.Figure.FromString(figure.SerializeToString())

    assert decoded.WhichOneof("value") == "number"
    assert decoded.number == 0.0
    assert decoded.source == PilotCard.FIGURE_SOURCE_TYPED


def test_a_figure_source_is_never_live():
    """§7.6.5. A live reading is shown BESIDE the master's figure, never written
    in. The moment a sensor value can be copied into a signed document silently,
    the signature stops meaning "the master said so"."""
    assert PilotCard.FigureSource.keys() == [
        "FIGURE_SOURCE_UNKNOWN",
        "FIGURE_SOURCE_REGISTRY",
        "FIGURE_SOURCE_TYPED",
    ]
    assert PilotCard.FIGURE_SOURCE_UNKNOWN == 0


def test_the_speed_table_cells_are_text():
    """Real cards write "85 rpm", "pitch 70%", "~12" and "n/a" in these cells;
    a number field would force every one of those into a lie."""
    fields = PilotCard.SpeedRow.DESCRIPTOR.fields_by_name
    for name in ("rpm_pitch", "loaded_knots", "ballast_knots"):
        assert fields[name].type == fields[name].TYPE_STRING


def test_the_parties_are_the_handovers():
    """Reused rather than paralleled: the same people write both records."""
    fields = PilotCard.DESCRIPTOR.fields_by_name
    for name in ("created_by", "master", "voided_by"):
        assert fields[name].message_type.full_name == "keelson.ChecklistHandover.Party"
    assert (
        fields["vessel"].message_type.full_name == "keelson.ChecklistHandover.VesselRef"
    )


def test_the_pilots_receipt_is_not_a_second_signature():
    """§7.6.2. Only the master signs; the pilot's acknowledgement carries a name
    and an instant and never touches the body."""
    assert _field_names(PilotCard.PilotAcknowledgement) == {"name", "acknowledged_at"}
    assert (
        PilotCard.DESCRIPTOR.fields_by_name["pilot"].message_type.full_name
        == "keelson.PilotCard.PilotAcknowledgement"
    )


def test_a_correction_is_a_new_card_that_names_the_old_one():
    """§7.6.3. Both ends of the revision chain, so a printed card always
    matches a stored one and "the current card" is computable."""
    fields = PilotCard.DESCRIPTOR.fields_by_name
    assert fields["revision_of"].type == fields["revision_of"].TYPE_STRING
    assert fields["superseded_by"].type == fields["superseded_by"].TYPE_STRING


def test_the_signature_and_the_receipt_are_two_instants():
    names = _field_names(PilotCard)
    assert "signed_at" in names
    assert "acknowledged_at" in _field_names(PilotCard.PilotAcknowledgement)


def test_a_card_does_not_carry_a_command_lease():
    """A card moves nothing. A record that carried lease machinery would invite
    a consumer to conflate the master's signature with the conn."""
    names = _field_names(PilotCard)
    assert "token" not in names
    assert "lease_ttl_seconds" not in names
    assert "heartbeat_interval_seconds" not in names
    assert "released" not in names


def test_a_card_is_not_a_checklist_run():
    """The link the other way does not exist either: nothing on a checklist
    message names a card, so there is no second writer to a run's key."""
    from keelson.payloads.ChecklistState_pb2 import ChecklistState

    assert "run_id" not in _field_names(PilotCard)
    assert "card_id" not in _field_names(ChecklistState)
    assert "card_id" not in _field_names(ChecklistHandover)


def test_the_stance_is_the_default_by_decision():
    """qos.yaml says so beside checklist_procedure: written at human pace, read
    out of storage on mount, nothing waits on it in real time."""
    assert qos.profile_name_for("pilot_card") == "default"


def test_a_signed_card_survives_an_envelope_round_trip():
    """Behavioural. The whole record, through the wire, with an absence in it."""
    card = PilotCard(
        card_id="pc_1",
        status=PilotCard.CARD_STATUS_SIGNED,
        vessel=ChecklistHandover.VesselRef(realm="rise", entity_id="sf18"),
        vessel_name="SF18",
        particulars=PilotCard.Particulars(
            name=PilotCard.Figure(source=PilotCard.FIGURE_SOURCE_REGISTRY, text="SF18"),
            # No MMSI on the registry row: seeded as nothing, not as 0.
            mmsi=PilotCard.Figure(source=PilotCard.FIGURE_SOURCE_REGISTRY),
        ),
        drafts=PilotCard.Drafts(
            forward_m=PilotCard.Figure(
                source=PilotCard.FIGURE_SOURCE_TYPED, number=2.4
            ),
            aft_m=PilotCard.Figure(source=PilotCard.FIGURE_SOURCE_TYPED, number=2.9),
        ),
        equipment=[
            PilotCard.EquipmentItem(
                id="gyro",
                label="Gyro compass",
                status=PilotCard.EQUIPMENT_STATUS_DEFECT,
            ),
        ],
        defects=[
            PilotCard.Defect(
                id="d_1",
                equipment_id="gyro",
                description="Gyro reads 3° high",
                operational_effect="Steer by magnetic; ROT indicator unaffected",
            ),
        ],
        master=ChecklistHandover.Party(operator_id="master", username="A. Master"),
    )
    card.signed_at.FromSeconds(1_760_000_900)

    envelope = keelson.enclose(card.SerializeToString())
    _received_at, _enclosed_at, payload = keelson.uncover(envelope)
    decoded = PilotCard.FromString(payload)

    assert decoded.status == PilotCard.CARD_STATUS_SIGNED
    assert decoded.signed_at.seconds == 1_760_000_900
    assert decoded.drafts.forward_m.number == 2.4

    # The absence survived as an absence, not as a zero or an empty string.
    assert decoded.particulars.mmsi.WhichOneof("value") is None
    assert decoded.particulars.mmsi.source == PilotCard.FIGURE_SOURCE_REGISTRY

    # And the defect still names the line it is about.
    assert decoded.defects[0].equipment_id == decoded.equipment[0].id
