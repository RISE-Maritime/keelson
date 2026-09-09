"""The ellipsoid, checked against numbers from outside this repository.

Every assertion here is a published WGS84 figure rather than whatever the code
happened to produce, because a test written from the implementation's own
output would pass just as happily with the semi-major axis mistyped.
"""

import math

import pytest

from keelson.geodesy import WGS84_A, WGS84_F, TangentPlane


def test_the_ellipsoid_is_wgs84():
    """The two defining parameters. Everything else is derived from them, so a
    typo here is a typo in every distance the module produces."""
    assert WGS84_A == 6_378_137.0
    assert 1.0 / WGS84_F == pytest.approx(298.257223563)


@pytest.mark.parametrize(
    "latitude, m_per_deg_lat, m_per_deg_lon",
    [
        (0.0, 110_574.3, 111_319.5),
        (45.0, 111_131.7, 78_846.8),
        (57.6, 111_370.5, 59_790.8),  # the Gothenburg approaches
    ],
)
def test_metres_per_degree_matches_the_standard_series(
    latitude, m_per_deg_lat, m_per_deg_lon
):
    """Reference values from the truncated Fourier series for WGS84 metres per
    degree — a different derivation entirely, not this code with the numbers
    copied out of it:

        lat: 111132.92 - 559.82 cos 2f + 1.175 cos 4f - 0.0023 cos 6f
        lon: 111412.84 cos f - 93.5 cos 3f + 0.118 cos 5f

    A metre of tolerance is the series' own truncation error, not slack.
    """
    plane = TangentPlane.at(latitude, 0.0)
    assert plane.m_per_deg_lat == pytest.approx(m_per_deg_lat, abs=1.0)
    assert plane.m_per_deg_lon == pytest.approx(m_per_deg_lon, abs=1.0)


def test_a_degree_of_latitude_grows_towards_the_pole():
    """The meridional radius of curvature increases with latitude — the earth
    is flatter there. A spherical model gets this exactly backwards by
    reporting no change at all, so it is the cheapest check that the ellipsoid
    is being used."""
    equator = TangentPlane.at(0.0, 0.0).m_per_deg_lat
    pole = TangentPlane.at(90.0, 0.0).m_per_deg_lat
    assert pole > equator


def test_a_degree_of_longitude_shrinks_towards_the_pole():
    """Which is the whole reason a distance cannot be applied to degrees
    directly: the same offset in metres is a different number of degrees east
    than it is north, everywhere except one latitude."""
    plane = TangentPlane.at(60.0, 0.0)
    assert plane.m_per_deg_lon == pytest.approx(
        TangentPlane.at(0.0, 0.0).m_per_deg_lon * math.cos(math.radians(60.0)),
        rel=0.01,
    )


def test_the_conversion_is_invertible():
    """Linear by construction, so this holds to float rounding rather than to a
    tolerance somebody chose."""
    plane = TangentPlane.at(57.6, 11.8)
    east, north = plane.enu_from_wgs84(57.601, 11.801)
    latitude, longitude = plane.wgs84_from_enu(east, north)
    assert latitude == pytest.approx(57.601, abs=1e-12)
    assert longitude == pytest.approx(11.801, abs=1e-12)


def test_the_origin_is_the_zero_of_the_frame():
    plane = TangentPlane.at(57.6, 11.8)
    assert plane.enu_from_wgs84(57.6, 11.8) == (0.0, 0.0)


def test_north_and_east_have_the_signs_a_chart_has():
    """A sign error here is invisible in a round trip, which is why it gets its
    own check: increasing latitude is north, increasing longitude is east."""
    plane = TangentPlane.at(57.6, 11.8)
    east, north = plane.enu_from_wgs84(57.7, 11.9)
    assert east > 0.0
    assert north > 0.0
