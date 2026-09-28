"""Render messages/qos.yaml, commentary included, as docs/qos-profiles.md.

About 60% of qos.yaml is comment, and unlike subjects.yaml some of it has no
anchor in the parsed data at all: the `# NB:` blocks explain why particular
subjects are deliberately ABSENT from a profile, which is a statement no table
of present rows can make. Those blocks render immediately after the group they
qualify, which is where they sit in the file and where they read correctly.

Until now nothing generated a page from this file; only the profile *name*
reached the docs, as a column in subjects-and-types.md.
"""

import sys
import yaml
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import commented_yaml as cy  # noqa: E402

PROFILE_FIELDS = ("priority", "congestion_control", "reliability", "express")


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def main(args: argparse.Namespace) -> None:
    doc = yaml.safe_load(args.qos_yaml_path.read_text()) or {}
    profiles = doc.get("profiles") or {}
    subjects = doc.get("subjects") or {}
    default = doc.get("default") or "default"

    events = list(cy.walk(args.qos_yaml_path))
    cy.check_against(events, doc, args.qos_yaml_path)

    out = [
        "# QoS profiles",
        "",
        f"Generated from `messages/qos.yaml`. A subject not listed below"
        f" inherits the `{default}` profile, which is a decision rather than an"
        f" omission -- see the notes under each group.",
        "",
    ]
    rows = []
    header = None

    def flush():
        if not rows:
            return
        out.append("| " + " | ".join(header) + " |")
        out.append("|" + "|".join([" --- "] * len(header)) + "|")
        for row in rows:
            out.append("| " + " | ".join(row) + " |")
        out.append("")
        rows.clear()

    for event in events:
        if isinstance(event, cy.Heading):
            flush()
            out.append(f"## {event.text}")
            out.append("")
        elif isinstance(event, cy.Prose):
            flush()
            out.extend(cy.render_prose(event.text))
            out.append("")
        elif isinstance(event, cy.Entry):
            # Top-level keys (`profiles:`, `subjects:`, `default:`) are
            # structure, not content: the sections they open are already
            # introduced by the file's own prose.
            if event.indent == 0:
                flush()
                continue
            if event.key in profiles and event.indent == 2:
                # A profile opens its own section; its four fields follow as
                # indented entries and are read from the parsed document rather
                # than from the stream, so a reordering cannot change the table.
                flush()
                out.append(f"### `{event.key}`")
                out.append("")
                spec = profiles[event.key] or {}
                out.append("| Field | Value |")
                out.append("| --- | --- |")
                for field in PROFILE_FIELDS:
                    if field in spec:
                        # YAML spelling, not Python's: the file says `true`.
                        value = spec[field]
                        if isinstance(value, bool):
                            value = "true" if value else "false"
                        out.append(f"| `{field}` | `{value}` |")
                out.append("")
            elif event.key in subjects and event.indent == 2:
                header = ["Subject", "Profile", "Notes"]
                rows.append(
                    [
                        f"`{event.key}`",
                        f"`{event.value}`",
                        md_escape(event.inline),
                    ]
                )
    flush()

    (args.output_path / "qos-profiles.md").write_text("\n".join(out).rstrip() + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="DocGenerator-QoS",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", type=int, default=logging.WARNING)
    parser.add_argument("qos_yaml_path", type=Path, help="Path to a qos.yaml file")
    parser.add_argument("output_path", type=Path, help="Folder to write output to.")
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    main(args)
