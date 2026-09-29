"""The comment scanner agrees with the YAML loader, and drops nothing.

`messages/subjects.yaml` and `messages/qos.yaml` are roughly half comment, and
that half is the only place the units, the `source_id` conventions, the
provenance and the deliberate omissions are written down. It used to be thrown
away by `yaml.safe_load` before the docs generator ever saw it (keelson#87).

Two properties are worth a test. The scanner must see exactly the keys the real
loader sees -- otherwise a subject silently vanishes from the page -- and every
comment in the source must come out somewhere in the stream, which is the thing
#87 was actually about.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
MESSAGES = REPO / "messages"
sys.path.insert(0, str(REPO / "scripts"))

import commented_yaml as cy  # noqa: E402

pytestmark = pytest.mark.skipif(
    not MESSAGES.is_dir(), reason="messages/ only exists in a repository checkout"
)

COMMENTED_FILES = ["subjects.yaml", "qos.yaml"]


def _words(text: str) -> list[str]:
    return [w for w in text.replace("`", "").split() if w]


@pytest.mark.parametrize("name", COMMENTED_FILES)
def test_scanner_sees_the_same_keys_as_the_loader(name):
    """The guard that licenses a hand-rolled scanner over a YAML library."""
    path = MESSAGES / name
    events = list(cy.walk(path))
    # Raises with the offending keys if the two ever disagree.
    cy.check_against(events, yaml.safe_load(path.read_text()), path)


@pytest.mark.parametrize("name", COMMENTED_FILES)
def test_no_comment_is_dropped(name):
    """keelson#87: every word of commentary reaches the generated page.

    Compared as words rather than lines, because a block is re-wrapped on the
    way out; what must not happen is a word going missing.
    """
    path = MESSAGES / name
    source = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            source += _words(stripped.lstrip("#").strip().strip("-# "))
        elif "#" in line:
            source += _words(line.partition("#")[2])

    emitted = []
    for event in cy.walk(path):
        if isinstance(event, (cy.Heading, cy.Prose)):
            emitted += _words(event.text)
        elif isinstance(event, cy.Entry) and event.inline:
            emitted += _words(event.inline)

    missing = [w for w in source if w not in emitted]
    assert not missing, f"{name}: comment text lost by the scanner: {missing[:10]}"


def test_subjects_yaml_has_section_headings():
    """Without the `### ... ###` markers the page is one undifferentiated list."""
    events = list(cy.walk(MESSAGES / "subjects.yaml"))
    headings = [e.text for e in events if isinstance(e, cy.Heading)]
    assert len(headings) > 10, headings
    assert "Route planning and navigation" in headings
    assert "Compute host health" in headings


def _walk_text(tmp_path, text):
    path = tmp_path / "sample.yaml"
    path.write_text(text)
    return list(cy.walk(path))


def test_both_heading_syntaxes(tmp_path):
    events = _walk_text(tmp_path, "### Subjects style ###\n# --- qos style ---\na: b\n")
    assert [e.text for e in events if isinstance(e, cy.Heading)] == [
        "Subjects style",
        "qos style",
    ]


def test_a_hash_inside_a_quoted_value_is_not_a_comment(tmp_path):
    events = _walk_text(tmp_path, 'a: "keep # this"  # but not this\n')
    entry = next(e for e in events if isinstance(e, cy.Entry))
    assert entry.value == '"keep # this"'
    assert entry.inline == "but not this"


def test_a_blank_line_separates_two_entry_runs(tmp_path):
    events = _walk_text(tmp_path, "a: 1\n\nb: 2\n")
    assert [type(e).__name__ for e in events] == ["Entry", "Blank", "Entry"]


def test_an_unparseable_line_fails_loudly(tmp_path):
    """Silently skipping it would mean the stream no longer describes the file."""
    with pytest.raises(ValueError, match="cannot parse line"):
        _walk_text(tmp_path, "a: 1\n- a list item\n")


def test_indented_runs_are_fenced_not_reflowed(tmp_path):
    """Key-expression examples and field vocabularies carry meaning in their
    alignment; markdown would run the columns together."""
    rendered = cy.render_prose("Intro line.\n\n  pc/disk/data   pc/net/eth0\n\nAfter.")
    assert "```text" in rendered
    assert "  pc/disk/data   pc/net/eth0" in rendered
    assert rendered[0] == "Intro line."
    assert rendered[-1] == "After."
