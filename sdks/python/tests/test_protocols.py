"""Protocol specifications (protocols/*.yaml) agree with the registry and with
themselves. See docs/protocol-specification.md §8 and scripts/protocols_lib.py.

The rules live in protocols_lib.validate so that the docs generator and this
test cannot disagree; this file only wires the SDK in as the resolver and turns
each problem into a failure with a readable address.
"""

import sys
from pathlib import Path

import keelson
import pytest

REPO = Path(__file__).resolve().parents[3]
PROTOCOLS = REPO / "protocols"
sys.path.insert(0, str(REPO / "scripts"))

from protocols_lib import Resolver, load_all, validate  # noqa: E402

pytestmark = pytest.mark.skipif(
    not PROTOCOLS.is_dir(), reason="protocols/ only exists in a repository checkout"
)


def _resolver() -> Resolver:
    return Resolver(
        subject_schema=keelson.get_subject_schema,
        message_class=keelson.get_protobuf_message_class_from_type_name,
        interface_known=keelson.is_interface_well_known,
    )


def test_there_is_at_least_one_protocol():
    assert load_all(PROTOCOLS), "protocols/ is empty"


def test_protocols_validate():
    problems = list(validate(load_all(PROTOCOLS), _resolver()))
    assert not problems, "\n" + "\n".join(str(p) for p in problems)


def test_validation_catches_a_default_valued_state(tmp_path):
    """The check that would have caught RISK_NONE = 0: a wire state must not be
    the proto3 default, whether the field is an enum or otherwise."""
    good = (PROTOCOLS / "command_authority.yaml").read_text()
    bad = good.replace(
        "      - name: held\n        field: released\n        is: false",
        "      - name: held\n        field: released\n        is: 0",
    )
    (tmp_path / "command_authority.yaml").write_text(bad)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("bool field needs true/false" in p for p in problems), problems


def test_validation_catches_a_transition_its_action_cannot_make(tmp_path):
    good = (PROTOCOLS / "command_authority.yaml").read_text()
    bad = good.replace(
        "      - {from: held, to: released, action: release}",
        "      - {from: held, to: released, action: heartbeat}",
    )
    assert bad != good
    (tmp_path / "command_authority.yaml").write_text(bad)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("does not satisfy state 'released'" in p for p in problems), problems


def test_validation_catches_a_slot_without_a_writer_field(tmp_path):
    good = (PROTOCOLS / "command_authority.yaml").read_text()
    bad = good.replace("    writer_field: controller_id\n", "")
    assert bad != good
    (tmp_path / "command_authority.yaml").write_text(bad)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("carries the writer" in p for p in problems), problems


def test_validation_catches_an_unquoted_comma_in_a_flow_note(tmp_path):
    """`{action: x, note: one, two}` silently parses `two` as a bare key —
    the note is truncated and nothing complains. Now something does."""
    good = (PROTOCOLS / "command_authority.yaml").read_text()
    bad = good.replace(
        "      - {action: request, note: Optional.}",
        "      - {action: request, note: Optional, tells the holder somebody is waiting.}",
    )
    assert bad != good
    (tmp_path / "command_authority.yaml").write_text(bad)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("unknown key" in p and "unquoted comma" in p for p in problems), problems


def test_validation_catches_two_lifecycles_for_one_subject_without_of(tmp_path):
    """`navigation_state` has a mode lifecycle and a sub_mode lifecycle. Without
    `of` they render under one heading and read as one machine."""
    good = (PROTOCOLS / "navigation_conduct.yaml").read_text()
    bad = good.replace("    of: sub_mode\n", "")
    assert bad != good
    (tmp_path / "navigation_conduct.yaml").write_text(bad)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("names the field each one follows" in p for p in problems), problems


def test_validation_catches_an_of_that_is_not_a_field(tmp_path):
    good = (PROTOCOLS / "navigation_conduct.yaml").read_text()
    bad = good.replace("    of: sub_mode\n", "    of: submode\n")
    assert bad != good
    (tmp_path / "navigation_conduct.yaml").write_text(bad)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any(
        "is not a field of keelson.NavigationState" in p for p in problems
    ), problems


def test_a_subject_may_have_a_key_row_per_role_slot_in_one_protocol():
    """`command_authority` is one subject with a slot per role — `{vessel_id}`,
    `{vessel_id}/overall`, … — so it has several key rows. That is one claim on
    the subject, not several."""
    problems = [str(p) for p in validate(load_all(PROTOCOLS), _resolver())]
    assert not any("claimed by more than one protocol" in p for p in problems), problems
    rows = [k for k in load_all(PROTOCOLS) if k.name == "command_authority"][0]
    assert (
        len([k for k in rows.data["keys"] if k["subject"] == "command_authority"]) > 1
    )
    assert rows.subjects == [
        "command_authority",
        "command_assignment",
        "command_request",
    ]


def test_validation_still_catches_two_protocols_claiming_one_subject(tmp_path):
    good = (PROTOCOLS / "command_authority.yaml").read_text()
    (tmp_path / "command_authority.yaml").write_text(good)
    (tmp_path / "second.yaml").write_text(
        good.replace("name: command_authority", "name: second")
    )
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("claimed by more than one protocol" in p for p in problems), problems


def test_a_protocol_may_set_role_conn(tmp_path):
    """ROLE_CONN is 1, not the proto default, so an action may assert it.
    When it was 0 the validator refused `sets: {role: ROLE_CONN}` as a default."""
    good = (PROTOCOLS / "command_authority.yaml").read_text()
    bad_zero = (
        "    sets: {released: false, kind: RECORD_KIND_LEASE}\n    note: A fresh token"
    )
    assert bad_zero in good
    with_role = good.replace(
        bad_zero,
        "    sets: {released: false, kind: RECORD_KIND_LEASE, role: ROLE_CONN}\n    note: A fresh token",
    )
    (tmp_path / "command_authority.yaml").write_text(with_role)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert not problems, problems
    unspecified = good.replace(
        bad_zero,
        "    sets: {released: false, kind: RECORD_KIND_LEASE, role: ROLE_UNSPECIFIED}\n    note: A fresh token",
    )
    (tmp_path / "command_authority.yaml").write_text(unspecified)
    problems = [str(p) for p in validate(load_all(tmp_path), _resolver())]
    assert any("proto3 default" in p for p in problems), problems
