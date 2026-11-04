"""Subject names follow the convention in protocol-specification.md §2.2.2.

The rules were written down long after most subjects were named, and until
keelson#88 nothing checked any of them: the only assertion anywhere was that a
subject is lowercase. That is how `radio_rssi` survived beside `radio_rssi_dbm`
for two releases -- two subjects for one quantity, one of them missing its unit.

Only the mechanical half is checkable, and only that is checked here. Whether a
subject has earned its existence (§2.2.1), and whether its `<entity>_<property>`
reads well, stay human judgement.

Both vocabularies -- the unit table and the list of subjects deliberately
without a unit -- are READ OUT OF §2.2.3 OF THE SPECIFICATION rather than
duplicated here, so the specification and `subjects.yaml` cannot drift apart --
the same arrangement `test_protocols.py` has with `protocols/*.yaml`. Only §2.2.3
is read: the rest of the document has tables of its own.
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
    "keelson.TimestampedFloatPeriod",
    "keelson.TimestampedDouble",
    "keelson.TimestampedInt",
    "keelson.TimestampedInt64",
}


def spec_section_223() -> str:
    """§2.2.3 alone: from its heading to the next heading of any level."""
    m = re.search(
        r"^#### 2\.2\.3 .*?$(.*?)(?=^#)", SPEC.read_text(), re.MULTILINE | re.DOTALL
    )
    assert m, "§2.2.3 not found in the specification"
    return m.group(1)


def units_from_spec() -> set[str]:
    """The unit vocabulary of §2.2.3, read from its table and nowhere else."""
    units = set(re.findall(r"^\| ([a-z0-9_]+)\s+\|", spec_section_223(), re.MULTILINE))
    assert {"m", "deg", "newton_meter"} <= units, "the §2.2.3 units table did not parse"
    return units


def dimensionless_from_spec() -> set[str]:
    """Subjects that take a generic numeric payload and still carry no unit,
    because the quantity has no dimension: the backticked names in §2.2.3's
    "Deliberately without a unit" paragraph. Adding to that paragraph is meant
    to be a conscious act, not a way past a failing test."""
    m = re.search(
        r"^\*\*Deliberately without a unit\.\*\*(.*?)(?:\n\n|\Z)",
        spec_section_223(),
        re.MULTILINE | re.DOTALL,
    )
    assert m, "the §2.2.3 'Deliberately without a unit' paragraph not found"
    names = set(re.findall(r"`([a-z0-9_]+)`", m.group(1)))
    assert names, "the §2.2.3 'Deliberately without a unit' paragraph lists nothing"
    return names


def unit_of(subject: str, units: set[str]) -> str | None:
    """The table entry the subject name ends in, as whole underscore-separated
    tokens: `moment_newton_meter` ends in `newton_meter`, not in `meter`. The
    longest match wins."""
    matches = [u for u in units if subject.endswith("_" + u)]
    return max(matches, key=len) if matches else None


def test_every_subject_is_lowercase_snake_case():
    bad = [s for s in keelson._SUBJECTS if not SUBJECT_RE.fullmatch(s)]
    assert not bad, f"not lowercase snake_case (§2.2.2): {bad}"


def test_a_dimensional_subject_ends_in_a_known_unit():
    """A bare number is not a measurement: `rudder_angle` could be degrees or
    radians and no consumer can tell."""
    units = units_from_spec()
    dimensionless = dimensionless_from_spec()
    bad = [
        subject
        for subject, proto in keelson._SUBJECTS.items()
        if proto in GENERIC_NUMERIC
        and subject not in dimensionless
        and unit_of(subject, units) is None
    ]
    assert not bad, (
        f"generic numeric payload with no unit from the §2.2.3 table: {bad}. "
        "Add the unit to the name, or, if the quantity really has none, to the "
        "'Deliberately without a unit' paragraph of §2.2.3."
    )


def test_every_unit_in_the_table_is_used():
    """The other direction: every unit the specification lists ends at least
    one subject, of any payload type -- the table's own examples include vector
    subjects such as `angular_velocity_radps`. A unit nobody uses is either
    stale or unreachable by the matching above, as `newton_meter` once was."""
    unused = sorted(
        u
        for u in units_from_spec()
        if not any(s.endswith("_" + u) for s in keelson._SUBJECTS)
    )
    assert not unused, f"units in §2.2.3 that no subject uses: {unused}"


def test_no_command_subjects():
    """§2.2.2: a command is an RPC, not a subject. A `cmd_*` subject has no
    reply, no result and no authority check -- see connectors/CLAUDE.md."""
    bad = [s for s in keelson._SUBJECTS if s.startswith("cmd_") or s.endswith("_cmd")]
    assert not bad, f"command-shaped subjects; use an RPC interface (§3): {bad}"


def test_dimensionless_allowlist_has_no_dead_entries():
    """An allowlist that outlives the subject it excused is a quiet lie."""
    gone = dimensionless_from_spec() - set(keelson._SUBJECTS)
    assert not gone, (
        "§2.2.3 'Deliberately without a unit' names subjects that no longer "
        f"exist: {sorted(gone)}"
    )
