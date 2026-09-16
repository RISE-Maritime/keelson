"""The contract between collectors.py, SUBJECTS_BY_GROUP and the registry.

Every subject the collectors can emit must be in keelson's subject registry
with a payload type this connector knows how to encode. Without these tests a
typo in a subject name degrades quietly: construct_pubsub_key only logs a
warning, and the key still goes on the bus -- where foxglove and mcap drop it
because they cannot resolve a schema.
"""

import ast

import pytest

import keelson
from pc import collectors, publishing
from pc.collectors import EMITTED_SUBJECTS

HOST_SUBJECTS = {
    "host_name",
    "host_boot_time",
    "cpu_load_pct",
    "cpu_temperature_celsius",
    "memory_used_pct",
    "swap_used_pct",
    "disk_used_pct",
    "disk_free_bytes",
    "network_interface_up",
}


def subject_literals_in_collector_code() -> set:
    """Every string literal naming a well-known subject in collectors.py,
    excluding the SUBJECTS_BY_GROUP declaration itself.

    An AST walk rather than a regex, because the module is full of other string
    constants (filesystem types, sensor chip names); intersecting with the
    registry is what picks out the subjects.
    """
    with open(collectors.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    declaration = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "SUBJECTS_BY_GROUP"
        ):
            declaration = {id(n) for n in ast.walk(node)}

    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in declaration
        and keelson.is_subject_well_known(node.value)
    }


def test_emitted_subjects_are_exactly_the_host_subjects():
    assert EMITTED_SUBJECTS == HOST_SUBJECTS


def test_declaration_matches_the_code():
    """SUBJECTS_BY_GROUP feeds the liveliness tokens. If it drifts from what
    the collectors actually name, the tokens advertise the wrong capability."""
    in_code = subject_literals_in_collector_code()
    assert in_code - EMITTED_SUBJECTS == set(), "emitted by code, not declared"
    assert EMITTED_SUBJECTS - in_code == set(), "declared, but no code emits it"


@pytest.mark.parametrize("subject", sorted(HOST_SUBJECTS))
def test_subject_is_well_known_with_an_encoder(subject):
    assert keelson.is_subject_well_known(subject)
    assert keelson.get_subject_schema(subject) in publishing.ENCODERS


def test_disk_free_bytes_is_int64():
    """A byte count on a TimestampedInt (int32) overflows silently past 2 GiB.
    Pin the type so nobody 'simplifies' it back."""
    assert keelson.get_subject_schema("disk_free_bytes") == "keelson.TimestampedInt64"
