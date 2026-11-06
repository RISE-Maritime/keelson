"""Key shape and storage per subject — the SDK view of ``subject_keys.yaml``.

``subjects.yaml`` says what a subject carries; this says what its keys are:

- a **stream** — the key's tail is the producer, a handful of keys per subject;
- an **instance** — one key per record of a thing that exists many times;
- a **slot** — one position, overwritten;

and whether a deployment must **store** them (``latest``) or not (``none``).

A client discovering keys (a subscribe plus a GET on ``.../pubsub/**``) uses
:func:`is_record_subject` to keep a router storage's records out of its list of
streams. Like :mod:`keelson.qos`, this module imports no transport.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, NamedTuple

import yaml

logger = logging.getLogger("keelson")

_PACKAGE_ROOT = Path(__file__).parent

SHAPES = ("stream", "instance", "slot")
STORAGES = ("none", "latest", "history")


class SubjectKey(NamedTuple):
    """One key template of a subject: the tail after ``{subject}/``."""

    key: str  # e.g. "{producer}", "{route_id}/edition/{route_edition_number}"
    shape: str  # "stream" | "instance" | "slot"
    storage: str  # "none" | "latest" | "history"


# The default when subject_keys.yaml is absent or a subject is not listed.
_DEFAULT: List[SubjectKey] = [SubjectKey("{producer}", "stream", "none")]
_SUBJECT_KEYS: Dict[str, List[SubjectKey]] = {}


def _row(subject: str, fields: dict) -> SubjectKey:
    row = SubjectKey(str(fields["key"]), str(fields["shape"]), str(fields["storage"]))
    if row.shape not in SHAPES:
        raise ValueError(f"{subject}: shape must be one of {SHAPES}, got {row.shape!r}")
    if row.storage not in STORAGES:
        raise ValueError(
            f"{subject}: storage must be one of {STORAGES}, got {row.storage!r}"
        )
    return row


def add_subject_keys(path: Path) -> None:
    """Load (or merge) key shapes from a subject_keys.yaml.

    A missing file is tolerated — every subject then resolves to the default
    stream — so the SDK stays importable before ``generate_python.sh`` has
    copied the bundled file into the package.
    """
    global _DEFAULT
    if not path.exists():
        logger.warning(
            "subject_keys.yaml not found at %s; every subject reads as a stream.", path
        )
        return
    with path.open() as fh:
        doc = yaml.safe_load(fh) or {}
    if doc.get("default"):
        _DEFAULT = [_row("default", f) for f in doc["default"]]
    for subject, rows in (doc.get("subjects") or {}).items():
        _SUBJECT_KEYS[subject] = [_row(subject, f) for f in rows]


def get_subject_keys(subject: str) -> List[SubjectKey]:
    """Every key template of ``subject``; the default stream if it is not listed."""
    return list(_SUBJECT_KEYS.get(subject, _DEFAULT))


def is_record_subject(subject: str) -> bool:
    """True if any of the subject's keys is a record (an instance or a slot)
    rather than a producer's stream."""
    return any(k.shape != "stream" for k in get_subject_keys(subject))


def is_stored_subject(subject: str) -> bool:
    """True if a deployment must store any of the subject's keys."""
    return any(k.storage != "none" for k in get_subject_keys(subject))


add_subject_keys(_PACKAGE_ROOT / "subject_keys.yaml")
