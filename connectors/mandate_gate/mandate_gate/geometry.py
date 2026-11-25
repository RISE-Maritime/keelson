"""Plane geometry for the mandate gate. Pure: no I/O, no clock, no zenoh.

Everything is worked on a local tangent plane centred on the vessel, in metres
east and north. The areas a gate is configured with are tens of kilometres at
most, where the plane's error is far below any margin a deployment would set.

A position is FORBIDDEN to a watch when it lies in at least one mandate area
and in none whose `roc_sites` names that watch. Water outside every area is
open to any watch: an area is declared where a mandate is, and only there does
it restrict. Two overlapping areas held by different sites form a shared
corridor, open to both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Mean Earth radius, metres (IUGG). The plane is local, so the choice of
#: radius moves nothing a margin would notice.
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Area:
    """One mandate area: its id, the ROC sites that hold it, its ring in (lat, lon)."""

    area_id: str
    roc_sites: frozenset[str]
    ring: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Plane:
    """A local tangent plane at (lat0, lon0)."""

    lat0: float
    lon0: float

    def to_en(self, lat: float, lon: float) -> tuple[float, float]:
        k = math.pi / 180.0 * EARTH_RADIUS_M
        return (
            (lon - self.lon0) * k * math.cos(math.radians(self.lat0)),
            (lat - self.lat0) * k,
        )

    def to_latlon(self, east: float, north: float) -> tuple[float, float]:
        k = math.pi / 180.0 * EARTH_RADIUS_M
        return (
            self.lat0 + north / k,
            self.lon0 + east / (k * math.cos(math.radians(self.lat0))),
        )


def contains(ring: list[tuple[float, float]], p: tuple[float, float]) -> bool:
    """Whether `p` is inside the polygon `ring` (even-odd; the ring need not close)."""
    x, y = p
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def _segment_distance(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = (
        0.0
        if length2 == 0
        else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    )
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _ray_hits(origin, unit, a, b) -> float | None:
    """The distance along the ray `origin + t·unit` (t ≥ 0) at which it cuts segment a-b."""
    ox, oy = origin
    ux, uy = unit
    ex, ey = b[0] - a[0], b[1] - a[1]
    denom = ux * ey - uy * ex
    if abs(denom) < 1e-12:
        return None
    wx, wy = a[0] - ox, a[1] - oy
    t = (wx * ey - wy * ex) / denom
    s = (wx * uy - wy * ux) / denom
    if t >= 0.0 and 0.0 <= s <= 1.0:
        return t
    return None


@dataclass(frozen=True)
class Projected:
    """The areas on one plane, ready to be asked about."""

    plane: Plane
    areas: tuple[tuple[Area, tuple[tuple[float, float], ...]], ...]

    @classmethod
    def of(cls, areas: list[Area], lat0: float, lon0: float) -> "Projected":
        plane = Plane(lat0, lon0)
        return cls(
            plane,
            tuple(
                (a, tuple(plane.to_en(lat, lon) for lat, lon in a.ring)) for a in areas
            ),
        )

    def forbidding(self, p: tuple[float, float], watch: str) -> Area | None:
        """The area that makes `p` forbidden to `watch`, or None when `p` is open to it."""
        holding = None
        for area, ring in self.areas:
            if contains(ring, p):
                if watch in area.roc_sites:
                    return None
                holding = holding or area
        return holding

    def clearance(self, p: tuple[float, float], watch: str) -> float:
        """How far `p` is from the nearest edge of an area `watch` does not hold.

        Conservative by construction: the edge of an area this watch does not
        hold is not always the edge of forbidden water — a corridor it shares
        with an area it does hold is open — so this may say nearer than it is,
        which only ever holds her further back.
        """
        best = math.inf
        for area, ring in self.areas:
            if watch in area.roc_sites:
                continue
            n = len(ring)
            for i in range(n):
                best = min(best, _segment_distance(p, ring[i], ring[(i + 1) % n]))
        return best

    def first_entry(
        self, origin: tuple[float, float], course_deg: float, watch: str, reach_m: float
    ) -> tuple[float, Area] | None:
        """Where, within `reach_m` along `course_deg` from `origin`, she first enters
        water forbidden to `watch`: `(distance_m, area)`, or None.

        `(0, area)` when `origin` is already in it.
        """
        here = self.forbidding(origin, watch)
        if here is not None:
            return 0.0, here
        rad = math.radians(course_deg)
        unit = (math.sin(rad), math.cos(rad))
        cuts = []
        for _, ring in self.areas:
            n = len(ring)
            for i in range(n):
                t = _ray_hits(origin, unit, ring[i], ring[(i + 1) % n])
                if t is not None and t <= reach_m:
                    cuts.append(t)
        cuts.sort()
        # Walk the stretches between successive edge crossings, asking of each
        # one's midpoint; the first forbidden stretch begins at its crossing.
        bounds = [0.0, *cuts, reach_m]
        for start, end in zip(bounds, bounds[1:]):
            if end - start < 1e-6:
                continue
            mid = (start + end) / 2.0
            area = self.forbidding(
                (origin[0] + unit[0] * mid, origin[1] + unit[1] * mid), watch
            )
            if area is not None:
                return start, area
        return None

    def along(
        self, origin, course_deg: float, distance_m: float
    ) -> tuple[float, float]:
        rad = math.radians(course_deg)
        return (
            origin[0] + math.sin(rad) * distance_m,
            origin[1] + math.cos(rad) * distance_m,
        )
