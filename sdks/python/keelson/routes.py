"""Route helpers: the §6 choreography of the protocol specification as library code.

Every rule that makes a route safe on the bus is written in prose in
``docs/protocol-specification.md`` §6, and every producer used to re-implement
it by hand, with failure modes that are silent rather than loud. These helpers
are that prose, once:

- the **edition store** rides in the ``source_id`` position
  (``route/{route_id}/edition/{N}``, ``route/{route_id}/latest``) — the key
  builders and :func:`parse_route_key` (§6.3);
- an **edition bump** is three publishes in a load-bearing order —
  :func:`bump_edition` (§6.4), guarded by :func:`edition_conflict`;
- a **signature** is verified over the stored ``Envelope.payload`` bytes,
  never a re-serialization — :func:`edition_signing_input` (§6.3.1);
- a **lease** expires on the receiver's own clock, never by comparing
  ``expires_at`` — :class:`LeaseTracker` (§6.5.1);
- **waypoint defaults** are merged by field presence —
  :func:`merge_waypoint_defaults` (§6.2).

Everything here is pure and transport-neutral: it builds keys and bytes and
decides, and never touches a session. The zenoh side — reading the store,
publishing a bump in order, resolving a ``RouteRef`` — is
:mod:`keelson.scaffolding.routes`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, NamedTuple, Optional, Tuple

from . import construct_pubsub_key, enclose, parse_pubsub_key

ROUTE = "route"
ROUTE_CHANGE_EVENT = "route_change_event"
ROUTE_SIGNATURE = "route_signature"
ROUTE_STATUS = "route_status"
ROUTE_EDIT_AUTHORITY = "route_edit_authority"
ROUTE_EDIT_REQUEST = "route_edit_request"

EDITION = "edition"
LATEST = "latest"


# ---------------------------------------------------------------------------
# Keys (§6.3). The discriminator lives in the source_id position.
# ---------------------------------------------------------------------------


def _chunk(name: str, value: str) -> str:
    """A route id or change id is exactly one key chunk: a composite id
    publishes without error and is never matched by the storage's
    ``.../route/*/...`` expressions."""
    if not isinstance(value, str) or not value or "/" in value or "*" in value:
        raise ValueError(f"{name} must be one non-empty key chunk, got {value!r}")
    return value


def _edition(edition: int) -> int:
    if not isinstance(edition, int) or isinstance(edition, bool) or edition < 1:
        raise ValueError(f"an edition is a positive integer, got {edition!r}")
    return edition


def construct_route_edition_key(
    base_path: str, entity_id: str, route_id: str, edition: int
) -> str:
    """``{base}/@v0/{entity}/pubsub/route/{route_id}/edition/{N}`` — immutable once written."""
    source = f"{_chunk('route_id', route_id)}/{EDITION}/{_edition(edition)}"
    return construct_pubsub_key(base_path, entity_id, ROUTE, source)


def construct_route_latest_key(base_path: str, entity_id: str, route_id: str) -> str:
    """``.../pubsub/route/{route_id}/latest`` — a copy of the highest edition."""
    source = f"{_chunk('route_id', route_id)}/{LATEST}"
    return construct_pubsub_key(base_path, entity_id, ROUTE, source)


def construct_route_change_event_key(
    base_path: str, entity_id: str, route_id: str, change_id: str
) -> str:
    """``.../pubsub/route_change_event/{route_id}/{change_id}`` — append-only."""
    source = f"{_chunk('route_id', route_id)}/{_chunk('change_id', change_id)}"
    return construct_pubsub_key(base_path, entity_id, ROUTE_CHANGE_EVENT, source)


def construct_route_signature_key(
    base_path: str, entity_id: str, route_id: str, edition: int
) -> str:
    """``.../pubsub/route_signature/{route_id}/edition/{N}`` — one per signed edition."""
    source = f"{_chunk('route_id', route_id)}/{EDITION}/{_edition(edition)}"
    return construct_pubsub_key(base_path, entity_id, ROUTE_SIGNATURE, source)


def construct_route_status_key(base_path: str, entity_id: str, route_id: str) -> str:
    """``.../pubsub/route_status/{route_id}`` — latest wins, not persisted."""
    return construct_pubsub_key(
        base_path, entity_id, ROUTE_STATUS, _chunk("route_id", route_id)
    )


def construct_route_edit_authority_key(
    base_path: str, entity_id: str, route_id: str
) -> str:
    """``.../pubsub/route_edit_authority/{route_id}`` — the edit lease (§6.5)."""
    return construct_pubsub_key(
        base_path, entity_id, ROUTE_EDIT_AUTHORITY, _chunk("route_id", route_id)
    )


def construct_route_edit_request_key(
    base_path: str, entity_id: str, route_id: str
) -> str:
    """``.../pubsub/route_edit_request/{route_id}`` — transient."""
    return construct_pubsub_key(
        base_path, entity_id, ROUTE_EDIT_REQUEST, _chunk("route_id", route_id)
    )


class RouteKey(NamedTuple):
    """A key of the edition store, parsed."""

    base_path: str
    entity_id: str
    route_id: str
    kind: str  # "edition" or "latest"
    edition: Optional[int]  # set for "edition", None for "latest"


def parse_route_key(key: str) -> Optional[RouteKey]:
    """Parse ``route/{route_id}/edition/{N}`` or ``route/{route_id}/latest``.

    Returns ``None`` for any other key — another subject, or a ``route`` key of
    another shape (a vessel's declared route at ``route/{source}`` has no
    editions) — rather than guessing.
    """
    try:
        parsed = parse_pubsub_key(key)
    except ValueError:
        return None
    if parsed["subject"] != ROUTE or parsed.get("target_id"):
        return None
    parts = parsed["source_id"].split("/")
    if len(parts) == 2 and parts[1] == LATEST and parts[0]:
        return RouteKey(
            parsed["base_path"], parsed["entity_id"], parts[0], LATEST, None
        )
    if len(parts) == 3 and parts[1] == EDITION and parts[0] and parts[2].isdigit():
        edition = int(parts[2])
        if edition >= 1:
            return RouteKey(
                parsed["base_path"], parsed["entity_id"], parts[0], EDITION, edition
            )
    return None


# ---------------------------------------------------------------------------
# The edition bump (§6.4).
# ---------------------------------------------------------------------------


class Conflict(NamedTuple):
    """Why an edition bump would overwrite somebody else's work."""

    kind: str  # "next-exists" or "latest-moved"
    local_edition: int
    stored_latest: Optional[int]
    message: str


def edition_conflict(
    local_edition: int, stored_latest: Optional[int], stored_next_exists: bool
) -> Optional[Conflict]:
    """Decide, from the store, whether bumping ``local_edition`` is safe.

    An edition is immutable once written (§6.3), and the edit lease is advisory
    (§6.5), so the store is read before every bump, lease or not:

    - ``next-exists`` — ``edition/{N+1}`` is already stored: somebody published
      it first, and writing it again would rewrite an immutable edition;
    - ``latest-moved`` — the stored ``latest`` is not N. Behind as well as
      ahead: a writer that cannot account for the store does not write past it.

    No stored ``latest`` is clean — a new route, or a store that holds nothing
    for it. Whether "nothing" means "nothing stored" or "no storage answering"
    is the caller's to know: see the §6.3 storage recipe.
    """
    if stored_next_exists:
        return Conflict(
            "next-exists",
            local_edition,
            stored_latest,
            f"edition {local_edition + 1} is already stored; reload before editing",
        )
    if stored_latest is not None and stored_latest != local_edition:
        return Conflict(
            "latest-moved",
            local_edition,
            stored_latest,
            f"the stored latest is edition {stored_latest}, not {local_edition}",
        )
    return None


@dataclass
class EditionBump:
    """A computed edition bump: the new edition, its change event, and the
    publications in the order they MUST go out (§6.4)."""

    route: object  # keelson.Route, edition N+1
    change_event: object  # keelson.RouteChangeEvent
    publications: List[Tuple[str, bytes]] = field(default_factory=list)


def _timestamp(message_field, now: Optional[datetime]) -> None:
    message_field.FromDatetime(now or datetime.now(timezone.utc))


def bump_edition(
    base_path: str,
    entity_id: str,
    route,
    *,
    author_id: str,
    author_site: str = "",
    reason: str = "edit",
    summary: str = "",
    now: Optional[datetime] = None,
    change_id: Optional[str] = None,
) -> EditionBump:
    """Compute the next edition of ``route`` and the three publications that
    publish it, in order (§6.4):

    1. ``route/{id}/edition/{N+1}`` — the new edition, immutable;
    2. ``route/{id}/latest`` — the pointer moves to it;
    3. ``route_change_event/{id}/{cid}`` — what changed, who, why.

    The order is load-bearing: the edition must exist before ``latest`` points
    at it. Publish ``publications`` in list order. Not atomic, by design: a
    publisher that dies after step 1 leaves an orphan edition (higher than
    ``latest``), which is inert and detectable, not a corrupt pointer.

    ``route`` is not modified. The caller should first check the store with
    :func:`edition_conflict` (``keelson.scaffolding.routes.publish_edition_bump``
    does both).
    """
    from .payloads.Route_pb2 import Route
    from .payloads.RouteChangeEvent_pb2 import RouteChangeEvent

    old = route.route_edition_number
    new_route = Route()
    new_route.CopyFrom(route)
    new_route.route_edition_number = old + 1
    _timestamp(new_route.modified_at, now)

    event = RouteChangeEvent(
        change_id=_chunk("change_id", change_id or str(uuid.uuid4())),
        route_id=new_route.route_id,
        old_edition=old,
        new_edition=old + 1,
        author_id=author_id,
        author_site=author_site,
        reason=reason,
        change_summary=summary,
    )
    _timestamp(event.at, now)

    route_bytes = enclose(new_route.SerializeToString())
    rid, n = new_route.route_id, new_route.route_edition_number
    publications = [
        (construct_route_edition_key(base_path, entity_id, rid, n), route_bytes),
        (construct_route_latest_key(base_path, entity_id, rid), route_bytes),
        (
            construct_route_change_event_key(
                base_path, entity_id, rid, event.change_id
            ),
            enclose(event.SerializeToString()),
        ),
    ]
    return EditionBump(new_route, event, publications)


# ---------------------------------------------------------------------------
# Signatures (§6.3.1).
# ---------------------------------------------------------------------------


def edition_signing_input(envelope: bytes) -> bytes:
    """The bytes a route signature signs: exactly the ``Envelope.payload`` stored
    at ``route/{route_id}/edition/{N}``.

    Pass the Envelope bytes as stored (a sample's payload, a ``get`` reply's
    payload). Never re-serialize a decoded ``Route`` to verify a signature:
    protobuf has no canonical encoding, so a round trip is not byte-stable and
    a valid signature fails, silently.
    """
    from .Envelope_pb2 import Envelope

    return bytes(Envelope.FromString(bytes(envelope)).payload)


# ---------------------------------------------------------------------------
# Waypoint defaults (§6.2).
# ---------------------------------------------------------------------------

_WAYPOINT_DEFAULTED = ("radius_m", "planned_sog_knots")


def _leg_defaultable_fields(leg_descriptor) -> List[str]:
    """The optional scalar fields of ``Leg``: the ones a default can fill.
    ``xtd_corridor_mode`` and the boundary lists are not optional, so they never
    come from the defaults (§6.2.2)."""
    return [
        f.name
        for f in leg_descriptor.fields
        if f.has_presence and f.message_type is None and not f.is_repeated
    ]


def merge_waypoint_defaults(route) -> list:
    """Return a copy of each waypoint with the route's defaults applied.

    A value present on the waypoint wins — **even 0**: the overridable fields
    are ``optional`` precisely so that an explicit zero is an answer, not an
    absence. An absent value takes ``Route.waypoints.defaults``:

    - ``radius_m`` and ``planned_sog_knots`` from the defaults;
    - each optional scalar of the waypoint's outgoing ``leg`` from
      ``defaults.leg`` — but only where the waypoint HAS an outgoing leg: every
      waypoint but the last on an open route, every waypoint on a closed one.

    The route is not modified. Waypoints are returned in order; address them by
    ``id``, never by index (§6.2).
    """
    from .payloads.Route_pb2 import ROUTE_TOPOLOGY_CLOSED, Waypoint

    waypoints = route.waypoints.waypoint
    has_defaults = route.waypoints.HasField("defaults")
    defaults = route.waypoints.defaults
    closed = route.waypoints.topology == ROUTE_TOPOLOGY_CLOSED
    leg_fields = (
        _leg_defaultable_fields(defaults.leg.DESCRIPTOR)
        if has_defaults and defaults.HasField("leg")
        else []
    )

    merged = []
    for i, wp in enumerate(waypoints):
        m = Waypoint()
        m.CopyFrom(wp)
        if has_defaults:
            for name in _WAYPOINT_DEFAULTED:
                if not m.HasField(name) and defaults.HasField(name):
                    setattr(m, name, getattr(defaults, name))
            if closed or i < len(waypoints) - 1:
                for name in leg_fields:
                    if defaults.leg.HasField(name) and not (
                        m.HasField("leg") and m.leg.HasField(name)
                    ):
                        setattr(m.leg, name, getattr(defaults.leg, name))
        merged.append(m)
    return merged


# ---------------------------------------------------------------------------
# The lease (§6.5, §6.5.1).
# ---------------------------------------------------------------------------


class LeaseTracker:
    """A receiver's view of one lease — ``route_edit_authority``, or any record
    of that shape (``token``, ``released``, ``lease_ttl_seconds``).

    **Expiry is decided on this receiver's own clock** (§6.5.1): every accepted
    message arms ``deadline = clock() + lease_ttl_seconds``, and the lease has
    lapsed when ``clock()`` passes it. ``expires_at``, ``granted_at`` and
    ``last_heartbeat`` are the holder's clock and are never compared. A message
    delayed in transit arms the deadline late — the safe direction.

    What :meth:`observe` accepts:

    - a release carrying the current token clears the lease (a release with any
      other token is ignored, §6.5);
    - a heartbeat with the current token re-arms it;
    - a record with another token is taken only when no lease is held or the
      held one has lapsed here — never by comparing the two holders' clocks.

    ``clock`` must be monotonic; it defaults to :func:`time.monotonic`.
    """

    def __init__(
        self,
        default_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default_ttl = float(default_ttl_seconds)
        self._clock = clock
        self._record = None
        self._deadline: Optional[float] = None

    def observe(self, record) -> bool:
        """Apply one received record. Returns whether it was accepted."""
        now = self._clock()
        held = self._record is not None and not self._lapsed(now)
        if record.released:
            if self._record is not None and record.token == self._record.token:
                self._record, self._deadline = None, None
                return True
            return False
        if held and record.token != self._record.token:
            return False
        copy = type(record)()
        copy.CopyFrom(record)
        self._record = copy
        ttl = record.lease_ttl_seconds or self._default_ttl
        self._deadline = now + float(ttl)
        return True

    def _lapsed(self, now: float) -> bool:
        return self._deadline is None or now > self._deadline

    def holder(self):
        """The lease in force, or ``None`` if none is held or it has lapsed here."""
        if self._record is None or self._lapsed(self._clock()):
            return None
        return self._record

    def expired(self) -> bool:
        """True when a lease was held and has lapsed on this clock (or none was)."""
        return self.holder() is None

    def remaining(self) -> Optional[float]:
        """Seconds until it lapses here; ``None`` when no lease is in force."""
        if self.holder() is None:
            return None
        return self._deadline - self._clock()
