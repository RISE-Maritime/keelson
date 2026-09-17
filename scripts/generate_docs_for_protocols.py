"""Render protocols/*.yaml into docs/protocols/*.md plus an index page.

Usage: generate_docs_for_protocols.py <protocols dir> <docs dir>

Each page follows the section order of protocol-specification.md §8. The
lifecycle becomes a Mermaid state diagram, each flow a Mermaid sequence
diagram, with lanes per role. Prose fields are copied through as markdown.
Validation problems are printed and fail the run, so the docs cannot be
generated from a file the tests would reject.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from protocols_lib import (  # noqa: E402
    Protocol,
    Resolver,
    action_kind,
    load_all,
    roles_of,
    rpc_target,
    validate,
)

PREFIX = "{base_path}/@v0/{entity_id}/pubsub/"


def _resolver() -> Resolver:
    try:
        import keelson
    except Exception:  # pragma: no cover - docs can build without the SDK
        return Resolver()
    return Resolver(
        subject_schema=keelson.get_subject_schema,
        message_class=keelson.get_protobuf_message_class_from_type_name,
        interface_known=keelson.is_interface_well_known,
    )


def _md_escape_cell(text: Any) -> str:
    return _yaml_scalar(text).replace("|", "\\|").replace("\n", " ")


def _mermaid(text: Any) -> str:
    """Mermaid label text: `;` ends a statement and `#` starts an entity, so
    both are written as entities. Newlines become spaces."""
    return (
        _yaml_scalar(text)
        .replace("#", "#35;")
        .replace(";", "#59;")
        .replace("\n", " ")
        .strip()
    )


def _yaml_scalar(value: Any) -> str:
    """Render a YAML scalar the way it was written: true/false, not True/False."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _action_label(name: str, a: Dict[str, Any]) -> str:
    kind = action_kind(a)
    if kind == "publish":
        return f"{name}: publish {a['publish']}"
    if kind == "get":
        return f"{name}: get {a['get']}"
    if kind == "rpc":
        tk, target = rpc_target(a)
        return f"{name}: rpc {target}" + (" (class)" if tk == "class" else "")
    return f"{name}: {a.get('clock', 'clock')}"


def render_state_diagram(lc: Dict[str, Any]) -> str:
    lines = ["stateDiagram-v2"]
    for st in lc.get("states") or []:
        if "derived" in st:
            lines.append(f"    {st['name']}: {_mermaid(st['name'])} (derived)")
    for t in lc.get("transitions") or []:
        src = t.get("from", "[*]")
        lines.append(f"    {src} --> {t['to']}: {_mermaid(t['action'])}")
    return "\n".join(lines)


def render_sequence_diagram(p: Protocol, flow: Dict[str, Any]) -> str:
    actions = p.data["actions"]
    lines = ["sequenceDiagram"]
    for role in p.data["roles"]:
        lines.append(f"    participant {role}")
    lines.append("    participant bus")
    for step in flow["steps"]:
        name = step["action"]
        a = actions[name]
        kind = action_kind(a)
        actor = step.get("role") or roles_of(a)[0]
        label = _mermaid(_action_label(name, a))
        if step.get("guard"):
            label += f" [{_mermaid(step['guard'])}]"
        if kind == "publish":
            lines.append(f"    {actor}->>bus: {label}")
        elif kind == "get":
            lines.append(f"    {actor}->>bus: {label}")
            lines.append(f"    bus-->>{actor}: last value")
        elif kind == "rpc":
            lines.append(f"    {actor}->>bus: {label}")
            lines.append(f"    bus-->>{actor}: reply")
        else:  # clock
            lines.append(f"    Note over {actor}: {label}")
        if step.get("note"):
            lines.append(f"    Note right of {actor}: {_mermaid(step['note'])}")
    return "\n".join(lines)


def render(p: Protocol) -> str:
    d = p.data
    out: List[str] = []
    out.append(f"# {d['title']}\n")
    out.append(f"`{d['name']}` — **{d['status']}**")
    if d.get("depends_on"):
        deps = ", ".join(f"[{x}]({x}.md)" for x in d["depends_on"])
        out.append(f" — depends on {deps}")
    out.append("\n\n## Purpose\n\n" + d["purpose"].strip() + "\n")

    out.append("\n## Roles\n\n| Role | Description | Reads |\n|---|---|---|")
    for role, spec in d["roles"].items():
        reads = ", ".join(f"`{s}`" for s in spec.get("reads", []) or [])
        out.append(
            f"| `{role}` | {_md_escape_cell(spec['description'].strip())} | {reads} |"
        )

    out.append(
        "\n\n## Keys\n\n| Subject | Payload | Key | Shape | Storage | Writer field | Rate |\n|---|---|---|---|---|---|---|"
    )
    for row in d["keys"]:
        out.append(
            f"| `{row['subject']}` | `{row['payload']}` | `{PREFIX}{row['subject']}/{row['key']}` "
            f"| {row['shape']} | {row['storage']} | {_md_escape_cell(row.get('writer_field', ''))} "
            f"| {_md_escape_cell(row.get('rate', ''))} |"
        )
    if d.get("key_notes"):
        out.append("\n" + d["key_notes"].strip() + "\n")

    out.append(
        "\n\n## Actions\n\n| Action | By | Does | Sets | Note |\n|---|---|---|---|---|"
    )
    for name, a in d["actions"].items():
        by = ", ".join(f"`{r}`" for r in roles_of(a))
        does = _action_label(name, a).split(": ", 1)[1]
        sets = ", ".join(
            f"`{k}={_yaml_scalar(v)}`" for k, v in (a.get("sets") or {}).items()
        )
        out.append(
            f"| `{name}` | {by} | {does} | {sets} | {_md_escape_cell(a.get('note', ''))} |"
        )

    out.append("\n\n## Lifecycle\n")
    for lc in d["lifecycle"]:
        out.append(f"\n### `{lc['subject']}`\n")
        out.append("| State | Recognised by |\n|---|---|")
        for st in lc.get("states") or []:
            if "derived" in st:
                how = "derived — " + _md_escape_cell(st["derived"].strip())
            elif "is" in st:
                how = f"`{lc['payload'].split('.')[-1]}.{st['field']}` is `{_yaml_scalar(st['is'])}`"
            else:
                how = f"`{lc['payload'].split('.')[-1]}.{st['field']}` is {'set' if st['present'] else 'unset'}"
            out.append(f"| `{st['name']}` | {how} |")
        out.append("\n```mermaid\n" + render_state_diagram(lc) + "\n```\n")
        out.append("| From | To | Action | By |\n|---|---|---|---|")
        for t in lc.get("transitions") or []:
            by = ", ".join(f"`{r}`" for r in roles_of(d["actions"][t["action"]]))
            out.append(f"| {t.get('from', '—')} | {t['to']} | `{t['action']}` | {by} |")

    out.append("\n\n## Flows\n")
    for f_name, flow in d["flows"].items():
        out.append(
            f"\n### {f_name.replace('_', ' ')}\n\n{flow['description'].strip()}\n"
        )
        out.append("```mermaid\n" + render_sequence_diagram(p, flow) + "\n```\n")

    out.append("\n## Invariants\n")
    for i, inv in enumerate(d["invariants"], 1):
        out.append(f"{i}. {inv.strip()}")

    out.append("\n\n## Not solved\n\n" + d["not_solved"].strip() + "\n")
    return "\n".join(out)


def render_index(protocols: List[Protocol]) -> str:
    out = [
        "# Protocols\n",
        "A protocol is a set of subjects and interfaces that only make sense together "
        "(protocol specification §8). Each page is generated from `protocols/{name}.yaml`.\n",
        "| Protocol | Status | Subjects |\n|---|---|---|",
    ]
    for p in protocols:
        subjects = ", ".join(f"`{s}`" for s in p.subjects)
        out.append(
            f"| [{p.data['title']}]({p.name}.md) | {p.data['status']} | {subjects} |"
        )
    return "\n".join(out) + "\n"


def render_nav(protocols: List[Protocol]) -> str:
    """SUMMARY.md for mkdocs-literate-nav: the section's sidebar entries,
    in the index table's order. `index.md` is not listed — literate-nav's
    `implicit_index` makes it the clickable section page (mkdocs-material
    `navigation.indexes`)."""
    out = [f"* [{p.data['title']}]({p.name}.md)" for p in protocols]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("protocols_dir", type=Path)
    ap.add_argument("docs_dir", type=Path)
    args = ap.parse_args()

    protocols = load_all(args.protocols_dir)
    problems = list(validate(protocols, _resolver()))
    for pr in problems:
        print(f"error: {pr}", file=sys.stderr)
    if problems:
        return 1

    out_dir = args.docs_dir / "protocols"
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in protocols:
        (out_dir / f"{p.name}.md").write_text(render(p))
    (out_dir / "index.md").write_text(render_index(protocols))
    (out_dir / "SUMMARY.md").write_text(render_nav(protocols))
    print(f"Rendered {len(protocols)} protocol(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
