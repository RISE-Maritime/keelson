"""Subject names follow the convention in protocol-specification.md §2.2.2.

The rules were written down long after most subjects were named, and until
keelson#88 nothing checked any of them: the only assertion anywhere was that a
subject is lowercase. That is how `radio_rssi` survived beside `radio_rssi_dbm`
for two releases -- two subjects for one quantity, one of them missing its unit.

Only the mechanical half is checkable, and only that is checked here. Whether a
subject has earned its existence (§2.2.1), and whether its `<entity>_<property>`
reads well, stay human judgement.

The unit vocabulary is READ OUT OF THE SPECIFICATION rather than duplicated
here, so the table in §2.2.3 and `subjects.yaml` cannot drift apart -- the same
arrangement `test_protocols.py` has with `protocols/*.yaml`.
"""

import re
from pathlib import Path

import keelson
import pytest

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "docs" / "protocol-specification.md"

pytestmark = pytest.mark.skipif(
    not SPEC.is_file(), reason="the specification only exists in a repository checkout"
)

# §2.2.2: lowercase snake_case, alphanumeric parts.
SUBJECT_RE = re.compile(r"[a-z0-9]+(_[a-z0-9]+)*")

# Payload types that carry no meaning of their own, so the subject name has to
# carry all of it -- including the unit. §2.2.2, "primitive payloads".
GENERIC_NUMERIC = {
    "keelson.TimestampedFloat",
    "keelson.TimestampedDouble",
    "keelson.TimestampedInt",
    "keelson.TimestampedInt64",
}

# Subjects that take a generic numeric payload and still carry no unit, because
# the quantity has no dimension. Enumerated in §2.2.3 as well; adding to this
# list is meant to be a conscious act, not a way past a failing test.
DIMENSIONLESS = {
    # identifiers
    "imo_number",
    "mmsi_number",
    "radio_cell_id",
    "radio_physical_cell_id",
    "radio_earfcn",
    # dimensionless ratios
    "location_fix_hdop",
    "location_fix_pdop",
    "location_fix_vdop",
    # counts
    "location_fix_satellites_used",
    "location_fix_satellites_visible",
    # coded statuses
    "gnss_aiding_status",
    "imu_system_status",
    "button_state_change",
}


def units_from_spec() -> set[str]:
    """The unit vocabulary of §2.2.3, read from the table itself."""
    units = {
        m.group(1)
        for m in re.finditer(r"^\| ([a-z0-9_]+)\s+\|", SPEC.read_text(), re.MULTILINE)
    }
    assert "m" in units and "deg" in units, "the §2.2.3 units table did not parse"
    return units


def test_every_subject_is_lowercase_snake_case():
    bad = [s for s in keelson._SUBJECTS if not SUBJECT_RE.fullmatch(s)]
    assert not bad, f"not lowercase snake_case (§2.2.2): {bad}"


def test_a_dimensional_subject_ends_in_a_known_unit():
    """A bare number is not a measurement: `rudder_angle` could be degrees or
    radians and no consumer can tell."""
    units = units_from_spec()
    bad = [
        subject
        for subject, proto in keelson._SUBJECTS.items()
        if proto in GENERIC_NUMERIC
        and subject not in DIMENSIONLESS
        and subject.rsplit("_", 1)[-1] not in units
    ]
    assert not bad, (
        f"generic numeric payload with no unit from the §2.2.3 table: {bad}. "
        "Add the unit to the name, or, if the quantity really has none, to "
        "DIMENSIONLESS here and to §2.2.3."
    )


def test_the_units_table_covers_every_unit_in_use():
    """The drift guard in the other direction: a subject may not introduce a
    unit the specification never documents."""
    units = units_from_spec()
    used = {
        subject.rsplit("_", 1)[-1]
        for subject, proto in keelson._SUBJECTS.items()
        if proto in GENERIC_NUMERIC and subject not in DIMENSIONLESS
    }
    assert not (
        used - units
    ), f"units used but absent from §2.2.3: {sorted(used - units)}"


def test_no_command_subjects():
    """§2.2.2: a command is an RPC, not a subject. A `cmd_*` subject has no
    reply, no result and no authority check -- see connectors/CLAUDE.md."""
    bad = [s for s in keelson._SUBJECTS if s.startswith("cmd_") or s.endswith("_cmd")]
    assert not bad, f"command-shaped subjects; use an RPC interface (§3): {bad}"


def test_dimensionless_allowlist_has_no_dead_entries():
    """An allowlist that outlives the subject it excused is a quiet lie."""
    gone = DIMENSIONLESS - set(keelson._SUBJECTS)
    assert (
        not gone
    ), f"DIMENSIONLESS names subjects that no longer exist: {sorted(gone)}"
