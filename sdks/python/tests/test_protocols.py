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


# ── dotted field paths and terminal transitions (container_control, #300) ──


def _container_control_with(tmp_path, old, new):
    good = (PROTOCOLS / "container_control.yaml").read_text()
    bad = good.replace(old, new)
    assert bad != good, f"fixture text not found: {old!r}"
    (tmp_path / "container_control.yaml").write_text(bad)
    return [str(p) for p in validate(load_all(tmp_path), _resolver())]


def test_a_dotted_state_resolves_through_a_repeated_message(tmp_path):
    """`containers.state` is the state of each element of `containers`: the
    path is followed and the leaf enum is checked, default value included."""
    problems = _container_control_with(
        tmp_path,
        "sets: {containers.state: CONTAINER_STATE_RUNNING}",
        "sets: {containers.state: CONTAINER_STATE_UNSPECIFIED}",
    )
    assert any("proto3 default" in p for p in problems), problems


def test_validation_catches_a_dotted_path_to_no_field(tmp_path):
    problems = _container_control_with(
        tmp_path,
        "{name: paused, field: containers.state,",
        "{name: paused, field: containers.nope,",
    )
    assert any("'containers.nope' is not a field" in p for p in problems), problems


def test_validation_catches_a_dotted_path_through_a_scalar(tmp_path):
    """Only a message field can be descended into; `sequence` is a uint64."""
    problems = _container_control_with(
        tmp_path,
        "{name: paused, field: containers.state,",
        "{name: paused, field: sequence.state,",
    )
    assert any("'sequence.state' is not a field" in p for p in problems), problems


def test_validation_catches_a_terminal_transition_with_nothing_to_end(tmp_path):
    problems = _container_control_with(
        tmp_path,
        "      - {from: dead, action: omit_container,",
        "      - {action: omit_container,",
    )
    assert any("needs from:" in p for p in problems), problems


def test_validation_catches_a_terminal_transition_its_action_cannot_make(tmp_path):
    """An RPC does not end a record; the subject's publisher dropping it does."""
    problems = _container_control_with(
        tmp_path,
        "      - {from: dead, action: omit_container,",
        "      - {from: dead, action: remove,",
    )
    assert any("cannot end a record" in p for p in problems), problems


def test_the_page_shows_what_causes_each_transition():
    """A transition's note is where it says what causes it -- an RPC, or nothing
    at all (a crash, a restart policy). Until #300 the generator dropped every
    transition note, so a page could not answer the question its lifecycle
    exists for."""
    from generate_docs_for_protocols import render

    (protocol,) = [p for p in load_all(PROTOCOLS) if p.name == "container_control"]
    page = render(protocol)
    assert "| From | To | Action | By | Note |" in page
    assert "crashes or is OOM-killed with no RPC" in page
    assert "| removing | — (ends) | `omit_container` |" in page
    assert "removing --> [*]: omit_container" in page


# ── the declared default (replay, #299) ─────────────────────────────────────

_STOPPED_EXCUSE = (
    "        default_ok: >-\n"
    "          ReplayStatus.State shipped with STOPPED = 0 before §8 forbade a\n"
    "          default state; renumbering a published enum breaks every client.\n"
)


def _replay_with(tmp_path, old, new):
    good = (PROTOCOLS / "replay.yaml").read_text()
    bad = good.replace(old, new)
    assert bad != good, f"fixture text not found: {old!r}"
    (tmp_path / "replay.yaml").write_text(bad)
    return [str(p) for p in validate(load_all(tmp_path), _resolver())]


def test_a_default_state_without_its_excuse_is_still_refused(tmp_path):
    """The exception is declared per state; without it STOPPED = 0 is refused,
    as a state and as the value report_stopped sets."""
    problems = _replay_with(tmp_path, _STOPPED_EXCUSE, "")
    assert any("states.stopped" in p and "proto3 default" in p for p in problems)
    assert any("report_stopped" in p and "proto3 default" in p for p in problems)


def test_a_proposed_protocol_may_not_keep_a_default_state(tmp_path):
    """A proposed protocol still has the enum to fix; only a shipped one may not."""
    problems = _replay_with(tmp_path, "status: as-built", "status: proposed")
    assert any("only an as-built protocol" in p for p in problems), problems


def test_an_excuse_on_a_value_that_is_not_the_default_is_refused(tmp_path):
    """A stale excuse -- the enum was fixed, the exception was not removed."""
    problems = _replay_with(
        tmp_path,
        "      - {name: paused, field: state, is: PAUSED}",
        "      - {name: paused, field: state, is: PAUSED, default_ok: no reason}",
    )
    assert any("is not the proto default" in p for p in problems), problems


def test_an_excuse_needs_its_reason(tmp_path):
    problems = _replay_with(tmp_path, _STOPPED_EXCUSE, "        default_ok: ''\n")
    assert any("needs the reason" in p for p in problems), problems
