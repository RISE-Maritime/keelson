"""The context of the leg being sailed, as evidence (keelson #414).

A capability envelope varies with context (#226): the wind a vessel may sail
in is lower in a port approach with heavy traffic than in open sea. The route
already labels each leg with its plannable context, and `route_execution`
already says which edition and leg are being sailed, so the context is
resolved here rather than published by anyone:

    route_execution.route_ref + current_leg_from_waypoint_id
      -> GET route/{route_id}/edition/{N}     (that edition, never `latest`)
      -> the waypoint the leg leaves (legs hang on their departure waypoint)
      -> its operational_context, and its leg's colreg_traffic_intensity and
         colreg_situation

An edition is immutable once written (spec §6.3), so a resolved edition is
cached for the life of the process. A leg, edition or label that cannot be
resolved is reported as unknown, which a context condition counts against the
claim.

Emergent context (the traffic actually around the vessel) and hazard zones
(geometry, not leg labels) are not resolved here; see the README.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger("warrant-aggregator.context")

# The facts a context condition may name, in model._context_facts order.
FACTS = ("operational_context", "traffic_intensity", "colreg_situation")


def _name(enum, number: int, prefix: str) -> Optional[str]:
    """An enum value's name without its prefix; None for UNSPECIFIED or an
    unknown number: an unlabelled leg says nothing about its context."""
    try:
        name = enum.Name(number)
    except ValueError:
        return None
    if name.endswith("_UNSPECIFIED"):
        return None
    return name.removeprefix(prefix)


def unknown_context() -> Dict[str, Optional[str]]:
    return {fact: None for fact in FACTS}


def leg_context(route, leg_from_waypoint_id: str) -> Dict[str, Optional[str]]:
    """The context facts of the leg leaving `leg_from_waypoint_id` in `route`."""
    from keelson.payloads.Route_pb2 import (
        ColregSituation,
        ColregTrafficIntensity,
        OperationalContext,
    )

    for waypoint in route.waypoints.waypoint:
        if waypoint.id != leg_from_waypoint_id:
            continue
        leg = waypoint.leg if waypoint.HasField("leg") else None
        return {
            "operational_context": _name(
                OperationalContext,
                waypoint.operational_context,
                "OPERATIONAL_CONTEXT_",
            ),
            "traffic_intensity": (
                _name(
                    ColregTrafficIntensity,
                    leg.colreg_traffic_intensity,
                    "COLREG_TRAFFIC_INTENSITY_",
                )
                if leg is not None
                else None
            ),
            "colreg_situation": (
                _name(ColregSituation, leg.colreg_situation, "COLREG_SITUATION_")
                if leg is not None
                else None
            ),
        }
    return unknown_context()


class ContextResolver:
    """route_execution -> (facts, detail), fetching each edition once.

    `fetch(route_id, edition) -> Route | None` does the storage read; the
    connector passes one over its zenoh session, a test passes a dict lookup.
    A failed fetch is not cached, so a storage that comes up later is read.
    """

    def __init__(self, fetch: Callable[[str, int], object]):
        self.fetch = fetch
        self.routes: Dict[Tuple[str, int], object] = {}

    def resolve(self, route_execution) -> Tuple[Dict[str, Optional[str]], str]:
        ref = route_execution.route_ref
        leg = route_execution.current_leg_from_waypoint_id
        if not route_execution.HasField("route_ref") or not ref.route_id or not leg:
            return unknown_context(), "no route or leg being sailed"
        key = (ref.route_id, ref.route_edition_number)
        route = self.routes.get(key)
        if route is None:
            route = self.fetch(*key)
            if route is None:
                return unknown_context(), (
                    f"route {ref.route_id} edition {ref.route_edition_number} "
                    "could not be resolved"
                )
            self.routes[key] = route
        where = (
            f"route {ref.route_id} edition {ref.route_edition_number}, "
            f"leg from {leg}"
        )
        return leg_context(route, leg), where


def zenoh_route_fetcher(session, realm: str, entity_id: str, timeout: float = 2.0):
    """A `fetch` for ContextResolver: GET the exact edition from storage."""
    import keelson
    from keelson.payloads.Route_pb2 import Route

    def fetch(route_id: str, edition: int):
        key = keelson.construct_pubsub_key(
            realm, entity_id, "route", f"{route_id}/edition/{edition}"
        )
        try:
            for reply in session.get(key, timeout=timeout):
                if reply.ok is None:
                    continue
                _r, _e, payload = keelson.uncover(reply.ok.payload.to_bytes())
                return Route.FromString(payload)
        except Exception:
            logger.exception("Failed to resolve %s", key)
        logger.warning("Route edition not in storage: %s", key)
        return None

    return fetch
