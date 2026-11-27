"""Who holds the vessel's watch, from the handover records. Pure.

The newest ACCEPTED `checklist_handover` for this vessel names the relief, and
the relief's `roc_site` is the site on watch. Before any such record, the
watch is the deployment's configured initial one.

A record is a snapshot keyed by its handover id: a later sample for the same id
replaces the earlier one, so a handover that went ACCEPTED and was then
superseded by a newer record is judged by its latest form.

Two wire forms are read. Crowsnest writes `keelson.ChecklistHandover` in an
Envelope since keelson-js 0.6.0-pre.16. Before that it wrote raw camelCase
JSON, and terminal records are never republished, so router storage can still
hold some; they are read so a gate started against an old store sees the same
watch a station does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Record:
    """What the gate needs of one handover record."""

    handover_id: str
    status: str  # "accepted", "offered", "pending_vessel", "refused", "cancelled", "expired", "unknown"
    vessel_entity_id: str
    to_site: str
    accepted_at_s: float | None
    timestamp_s: float | None


@dataclass(frozen=True)
class Watch:
    site: str
    handover_id: str  # empty while it is the initial watch


_STATUS_NAMES = {
    0: "unknown",
    1: "offered",
    2: "pending_vessel",
    3: "accepted",
    4: "refused",
    5: "cancelled",
    6: "expired",
}


def _iso_seconds(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def from_proto(handover) -> Record:
    """A `keelson.ChecklistHandover` as a Record."""

    def seconds(ts):
        return ts.seconds + ts.nanos / 1e9 if ts.seconds or ts.nanos else None

    return Record(
        handover_id=handover.handover_id,
        status=_STATUS_NAMES.get(handover.status, "unknown"),
        vessel_entity_id=handover.vessel.entity_id,
        to_site=handover.offered_to.roc_site.strip(),
        accepted_at_s=seconds(handover.accepted_at),
        timestamp_s=seconds(handover.timestamp),
    )


def from_json(raw: bytes) -> Record | None:
    """A legacy camelCase JSON record as a Record, or None when it is not one."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or not doc.get("handoverId"):
        return None
    to = doc.get("to") or doc.get("offeredTo") or {}
    vessel = doc.get("vessel") or {}
    return Record(
        handover_id=str(doc["handoverId"]),
        status=str(doc.get("status") or "unknown"),
        vessel_entity_id=str(vessel.get("entityId") or ""),
        to_site=str(to.get("rocSite") or "").strip(),
        accepted_at_s=_iso_seconds(doc.get("acceptedAt")),
        timestamp_s=_iso_seconds(doc.get("updatedAt")),
    )


def watch_of(records: dict[str, Record], entity_id: str, initial_site: str) -> Watch:
    """The site on her watch: the newest ACCEPTED record for her that names a site."""
    best = None
    for r in records.values():
        if r.status != "accepted" or r.vessel_entity_id != entity_id or not r.to_site:
            continue
        key = (r.accepted_at_s or 0.0, r.timestamp_s or 0.0, r.handover_id)
        if best is None or key > best[0]:
            best = (key, r)
    if best is None:
        return Watch(initial_site, "")
    return Watch(best[1].to_site, best[1].handover_id)
