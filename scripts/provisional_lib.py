"""messages/provisional.yaml: loading it, and marking its entries in the docs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

KINDS = ("interfaces", "procedures", "subjects")
ISSUES = "https://github.com/RISE-Maritime/keelson/issues"

Provisional = Dict[str, Dict[str, Dict[str, Any]]]


def load(path: Path) -> Provisional:
    with path.open() as fh:
        doc = yaml.safe_load(fh)
    return {kind: doc[kind] for kind in KINDS}


def mark(entry: Dict[str, Any] | None) -> str:
    """Inline mark for a table cell, list item or line; empty if stable."""
    if entry is None:
        return ""
    return (
        f' <strong class="provisional">⚠ Provisional</strong>'
        f" ([#{entry['issue']}]({ISSUES}/{entry['issue']}))"
    )


def notice(what: str) -> str:
    """Admonition explaining the marks, for the top of a page."""
    return (
        f'!!! warning "Some {what} are provisional"\n'
        f"    Those marked **⚠ Provisional** may change with a release note and be\n"
        f"    removed at any minor release. See the\n"
        f"    [protocol specification](protocol-specification.md#9-provisional-contracts).\n"
    )
