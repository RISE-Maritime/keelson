"""ChecklistProcedure.participation: shared or individual runs (keelson#469, protocol-specification.md §7.5)."""

from keelson.payloads.ChecklistProcedure_pb2 import ChecklistProcedure


def test_a_procedure_says_whether_its_runs_are_shared_or_individual():
    """§7.5: unspecified reads as shared, so every older procedure keeps its meaning."""
    field = ChecklistProcedure.DESCRIPTOR.fields_by_name["participation"]
    assert field.number == 10
    values = {v.name: v.number for v in field.enum_type.values}
    assert values == {
        "PARTICIPATION_UNSPECIFIED": 0,
        "PARTICIPATION_SHARED": 1,
        "PARTICIPATION_INDIVIDUAL": 2,
    }
    assert ChecklistProcedure().participation == 0

    procedure = ChecklistProcedure(
        procedure_id="proc_003",
        participation=ChecklistProcedure.PARTICIPATION_INDIVIDUAL,
    )
    decoded = ChecklistProcedure.FromString(procedure.SerializeToString())
    assert decoded.participation == ChecklistProcedure.PARTICIPATION_INDIVIDUAL
