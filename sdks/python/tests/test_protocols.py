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
