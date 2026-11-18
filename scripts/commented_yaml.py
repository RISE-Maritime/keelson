"""Walk a YAML file in document order, keeping its comments.

`yaml.safe_load` throws comments away, which is fine for a consumer of the data
and useless for a document generator: in `messages/subjects.yaml` and
`messages/qos.yaml` roughly half the file is comment, and that half is the only
place the units, the `source_id` conventions, the provenance and the deliberate
omissions are written down. This module reads those files a second time, as
text, so the generators can render the prose beside the data it describes.

It is a line scanner rather than a round-trip YAML library on purpose. Both
files are a flat or two-level `key: value` mapping, so a scanner gives exact
document order and exact comment placement in about fifty lines -- where a
round-trip parser attaches a comment block to the *preceding* key and the
generator has to take it apart again. The cost is that the scanner understands
less YAML than the loader does, and `check_against` is what makes that safe:
the keys it found must be exactly the keys the real loader found, in the same
order, or the docs build fails rather than quietly dropping a subject.

The event stream is:

  Heading(text, level)            a marked section title
  Prose(text)                     any other run of `#` lines
  Entry(key, value, inline, indent)   one `key: value  # inline` line
  Blank()                         one blank line, so runs of entries can be
                                  grouped into separate tables

Two heading syntaxes are recognised, both of which the files already use:

  ### Well-known subjects ###                      subjects.yaml:1
  # --- realtime: operator inputs on the hot path ---   throughout qos.yaml
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Union

# `### Title ###` and `# --- Title ---`. The trailing run is required, so an
# ordinary comment that happens to start with dashes is not promoted.
_HEADING = re.compile(r"^#\s*(?:###?\s*(?P<h>.+?)\s*###?|---\s*(?P<d>.+?)\s*---)\s*$")
_COMMENT = re.compile(r"^\s*#\s?(?P<text>.*)$")
_ENTRY = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_./-]*)\s*:(?P<rest>.*)$"
)


@dataclass
class Heading:
    text: str
    level: int


@dataclass
class Prose:
    text: str


@dataclass
class Entry:
    key: str
    value: str
    inline: str
    indent: int


@dataclass
class Blank:
    pass


Event = Union[Heading, Prose, Entry, Blank]


def _split_inline(rest: str) -> tuple[str, str]:
    """Split `  keelson.Route    # a note` into its value and its comment.

    Quote-aware: a `#` inside a quoted scalar is part of the value, not the
    start of a comment. Neither file relies on that today, but a scanner that
    got it wrong would corrupt a value silently rather than fail.
    """
    quote = ""
    for i, ch in enumerate(rest):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return rest[:i].strip(), rest[i + 1 :].strip()
    return rest.strip(), ""


def walk(path: Path) -> Iterator[Event]:
    """Yield the file's headings, prose, entries and blank lines, in order."""
    pending: List[str] = []

    def flush() -> Iterator[Event]:
        if pending:
            yield Prose("\n".join(pending).strip())
            pending.clear()

    for raw in path.read_text().splitlines():
        line = raw.rstrip()

        if not line.strip():
            yield from flush()
            yield Blank()
            continue

        heading = _HEADING.match(line.strip())
        if heading:
            yield from flush()
            yield Heading(heading.group("h") or heading.group("d"), 2)
            continue

        comment = _COMMENT.match(line)
        if comment:
            pending.append(comment.group("text").rstrip())
            continue

        entry = _ENTRY.match(line)
        if entry:
            yield from flush()
            value, inline = _split_inline(entry.group("rest"))
            yield Entry(
                key=entry.group("key"),
                value=value,
                inline=inline,
                indent=len(entry.group("indent")),
            )
            continue

        # A line the scanner does not understand. Not silently skipped: it
        # would mean the stream no longer describes the file.
        raise ValueError(f"{path}: cannot parse line: {line!r}")

    yield from flush()


def keys(events: List[Event], indent: int | None = None) -> List[str]:
    return [
        e.key
        for e in events
        if isinstance(e, Entry) and (indent is None or e.indent == indent)
    ]


def check_against(
    events: List[Event], loaded: dict, path: Path, indent: int = 0
) -> None:
    """Fail unless the scanner saw exactly what the real YAML loader saw.

    This is what licenses a hand-rolled scanner: any construct it reads
    differently from `yaml.safe_load` stops the docs build instead of dropping a
    subject from the page without saying so.
    """
    scanned = keys(events, indent=indent)
    expected = list(loaded)
    if scanned != expected:
        missing = [k for k in expected if k not in scanned]
        extra = [k for k in scanned if k not in expected]
        raise ValueError(
            f"{path}: the comment scanner disagrees with yaml.safe_load "
            f"(missing={missing}, unexpected={extra}, "
            f"order_differs={missing == [] and extra == []})"
        )


def render_prose(text: str) -> List[str]:
    """Turn one comment block into markdown lines.

    Ordinary lines are left alone: the source is hard-wrapped at about 80
    columns and markdown reflows it back into a paragraph, which is what the
    author meant. Lines that are *deliberately* indented are not -- they are
    key-expression examples and field vocabularies whose alignment carries the
    meaning, and reflowing them would run the columns together. Those runs are
    fenced instead.
    """
    lines: List[str] = []
    run: List[str] = []
    mode = None

    def flush() -> None:
        nonlocal mode
        if not run:
            return
        if mode == "code":
            while run and not run[-1].strip():
                run.pop()
            lines.extend(["```text", *run, "```"])
        else:
            lines.extend(run)
        run.clear()
        mode = None

    for line in text.split("\n"):
        indented = line.startswith("  ") and line.strip() != ""
        want = "code" if indented else "prose"
        # A blank line inside a fenced run keeps it together; elsewhere it
        # separates paragraphs.
        if not line.strip() and mode == "code":
            run.append(line)
            continue
        if mode is not None and want != mode:
            flush()
        mode = want
        run.append(line)
    flush()
    return lines
