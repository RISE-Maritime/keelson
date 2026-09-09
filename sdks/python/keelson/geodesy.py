"""WGS84 <-> local ENU tangent-plane conversion.

keelson carries the subjects that move latitude and longitude around —
`location_fix`, and everything derived from it — but no way to turn a distance
in metres into a distance in degrees. Every consumer that needs one writes its
own, and two copies of the ellipsoid agree with each other right up until
somebody corrects one of them. This is that arithmetic, once.

The tangent plane is linear: metres-per-degree is computed at an origin and
held constant, so the conversion is exactly invertible up to float rounding.
That is the right approximation for a harbour or a trial area — a few tens of
kilometres — and the wrong one for a passage. A caller working at that scale
wants a real projection, not this.

Conventions match the bus: WGS84 (EPSG:4326) in degrees, local ENU in metres
east and north.

    >>> plane = TangentPlane.at(57.6, 11.8)
    >>> east, north = plane.enu_from_wgs84(57.601, 11.801)
    >>> plane.wgs84_from_enu(east, north)
    (57.601, 11.801)

Pure arithmetic — no I/O, no wall clock, no zenoh — so it is safe to call from
inside a measurement model or a deterministic simulation step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Semi-major axis of the WGS84 ellipsoid, in metres.
WGS84_A = 6_378_137.0

#: Flattening of the WGS84 ellipsoid.
WGS84_F = 1.0 / 298.257223563

_E2 = WGS84_F * (2.0 - WGS84_F)


@dataclass(frozen=True)
class TangentPlane:
    """A local ENU frame anchored at (lat0, lon0).

    `m_per_deg_lat` and `m_per_deg_lon` are the useful part on their own: a
    standard deviation, an offset or an error budget is a distance, and
    applying it to degrees directly makes the east component shrink towards
    the poles and disagree with the north component everywhere.
    """

    lat0_deg: float
    lon0_deg: float
    m_per_deg_lat: float
    m_per_deg_lon: float

    @classmethod
    def at(cls, lat0_deg: float, lon0_deg: float) -> TangentPlane:
        """Anchor a plane at a position.

        Metres-per-degree of latitude uses the meridional radius of curvature
        and metres-per-degree of longitude the prime vertical, both evaluated
        at `lat0_deg`. A spherical earth would put both within about half a
        percent, which is a metre every two hundred — enough to matter for a
        receiver whose error is being modelled in metres.
        """
        s = math.sin(math.radians(lat0_deg))
        denom = 1.0 - _E2 * s * s
        meridional = WGS84_A * (1.0 - _E2) / denom**1.5
        prime_vertical = WGS84_A / math.sqrt(denom)
        return cls(
            lat0_deg=lat0_deg,
            lon0_deg=lon0_deg,
            m_per_deg_lat=math.radians(1.0) * meridional,
            m_per_deg_lon=math.radians(1.0)
            * prime_vertical
            * math.cos(math.radians(lat0_deg)),
        )

    def enu_from_wgs84(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        """Metres east and north of the origin."""
        east = (lon_deg - self.lon0_deg) * self.m_per_deg_lon
        north = (lat_deg - self.lat0_deg) * self.m_per_deg_lat
        return east, north

    def wgs84_from_enu(self, east_m: float, north_m: float) -> tuple[float, float]:
        """Degrees latitude and longitude at an offset from the origin."""
        lat = self.lat0_deg + north_m / self.m_per_deg_lat
        lon = self.lon0_deg + east_m / self.m_per_deg_lon
        return lat, lon
