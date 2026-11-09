"""Route helpers over a zenoh session: read the edition store, publish an edition
bump in order, resolve a ``RouteRef`` (protocol specification §6).

The decisions are :mod:`keelson.routes` (pure); this module only does the I/O.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .. import uncover
from ..routes import (
    Conflict,
    EditionBump,
    bump_edition,
    construct_route_edition_key,
    construct_route_latest_key,
    edition_conflict,
)
from .qos_zenoh import put

logger = logging.getLogger("keelson.routes")


class EditionConflict(Exception):
    """An edition bump refused because the store does not match the local
    edition (:func:`keelson.routes.edition_conflict`). Nothing was published."""

    def __init__(self, conflict: Conflict) -> None:
        super().__init__(conflict.message)
        self.conflict = conflict


class DanglingRouteRef(LookupError):
    """A ``RouteRef`` whose edition cannot be resolved. Surfaced, never resolved
    to ``latest`` (§6.2)."""


def _get_payloads(session, key: str, timeout: float) -> List[bytes]:
    """Every ok reply's Envelope bytes for ``key``."""
    out = []
    for reply in session.get(key, timeout=timeout):
        if reply.ok is not None:
            out.append(bytes(reply.ok.payload.to_bytes()))
    return out


def _decode_route(envelope: bytes):
    from ..payloads.Route_pb2 import Route

    uncovered = uncover(envelope)
    if uncovered is None:
        raise ValueError("not a keelson Envelope")
    return Route.FromString(uncovered[2])


def read_stored_edition(
    session, base_path: str, entity_id: str, route_id: str, timeout: float = 5.0
) -> Optional[int]:
    """The edition the store's ``route/{route_id}/latest`` holds, or ``None``.

    Several replies (more than one storage) resolve to the highest edition.
    ``None`` means no storage answered with one — which a missing storage looks
    identical to (§6.3: durability is the router's configuration).
    """
    key = construct_route_latest_key(base_path, entity_id, route_id)
    editions = [
        _decode_route(p).route_edition_number
        for p in _get_payloads(session, key, timeout)
    ]
    return max(editions) if editions else None


def publish_edition_bump(
    session,
    base_path: str,
    entity_id: str,
    route,
    *,
    author_id: str,
    author_site: str = "",
    reason: str = "edit",
    summary: str = "",
    check: bool = True,
    timeout: float = 5.0,
    now=None,
) -> EditionBump:
    """Publish the next edition of ``route`` — edition N+1, then ``latest``, then
    the change event (§6.4) — and return what was published.

    With ``check`` (the default) the store is read first, lease or not: if
    ``edition/{N+1}`` already exists or ``latest`` is not N, it raises
    :class:`EditionConflict` and publishes nothing. An edition is immutable once
    written; this is what stops two editors both writing N+1.
    """
    rid, n = route.route_id, route.route_edition_number
    if check:
        stored_latest = read_stored_edition(session, base_path, entity_id, rid, timeout)
        next_key = construct_route_edition_key(base_path, entity_id, rid, n + 1)
        stored_next = bool(_get_payloads(session, next_key, timeout))
        conflict = edition_conflict(n, stored_latest, stored_next)
        if conflict is not None:
            raise EditionConflict(conflict)

    bump = bump_edition(
        base_path,
        entity_id,
        route,
        author_id=author_id,
        author_site=author_site,
        reason=reason,
        summary=summary,
        now=now,
    )
    for key, payload in bump.publications:
        put(session, key, payload)
    logger.info(
        "Published route %s edition %d (%s)",
        rid,
        bump.route.route_edition_number,
        reason,
    )
    return bump


def resolve_route_ref(
    session, base_path: str, entity_id: str, ref, timeout: float = 5.0
) -> Tuple[object, bytes]:
    """Fetch the edition a ``keelson.RouteRef`` names: a ``get`` on
    ``route/{route_id}/edition/{N}`` (§6.3).

    Returns ``(route, envelope_bytes)``. Keep the Envelope bytes if you verify
    signatures: :func:`keelson.routes.edition_signing_input` takes them.

    Raises :class:`DanglingRouteRef` when no storage answers, or when what
    answers is not that route at that edition. It never falls back to
    ``latest``.
    """
    if not ref.route_id or ref.route_edition_number < 1:
        raise DanglingRouteRef(f"not a resolvable RouteRef: {ref}")
    key = construct_route_edition_key(
        base_path, entity_id, ref.route_id, ref.route_edition_number
    )
    for envelope in _get_payloads(session, key, timeout):
        route = _decode_route(envelope)
        if (route.route_id, route.route_edition_number) == (
            ref.route_id,
            ref.route_edition_number,
        ):
            return route, envelope
        logger.warning(
            "%s answered with route %s edition %d",
            key,
            route.route_id,
            route.route_edition_number,
        )
    raise DanglingRouteRef(
        f"route {ref.route_id!r} edition {ref.route_edition_number} is not stored"
    )
