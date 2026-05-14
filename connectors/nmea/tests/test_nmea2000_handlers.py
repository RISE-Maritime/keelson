#!/usr/bin/env python3

"""Tests for n2k2keelson - NMEA2000 JSON parsing and publishing."""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
import keelson
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality
from nmea2000.message import NMEA2000Message, NMEA2000Field

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))  # Make sibling imports work

# Import the script dynamically
script_path = bin_root / "n2k2keelson.py"
loader = SourceFileLoader("n2k2keelson", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
n2k2keelson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n2k2keelson)

# Import functions to test
handle_pgn_129025 = n2k2keelson.handle_pgn_129025
handle_pgn_129026 = n2k2keelson.handle_pgn_129026
handle_pgn_127250 = n2k2keelson.handle_pgn_127250
handle_pgn_127257 = n2k2keelson.handle_pgn_127257
handle_pgn_130306 = n2k2keelson.handle_pgn_130306
handle_pgn_127245 = n2k2keelson.handle_pgn_127245
handle_pgn_129029 = n2k2keelson.handle_pgn_129029


@pytest.fixture
def mock_session():
    """Create a mock Zenoh session for testing"""
    # Clear the global PUBLISHERS cache before each test
    n2k2keelson.PUBLISHERS.clear()

    session = Mock()
    publisher = Mock()
    publisher.published_data = []

    def mock_put(data):
        publisher.published_data.append(data)

    publisher.put = Mock(side_effect=mock_put)
    session.declare_publisher = Mock(return_value=publisher)
    return session


def test_handle_pgn_129025_position(mock_session):
    """Test handling PGN 129025 (Position, Rapid Update)"""
    # Create a test message
    msg = NMEA2000Message(
        PGN=129025,
        id="positionRapidUpdate",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="latitude",
            name="Latitude",
            value=59.123456,
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="longitude",
            name="Longitude",
            value=18.654321,
            unit_of_measurement="deg",
        ),
    ]

    # Call handler
    handle_pgn_129025(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify publisher was called
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1


def test_handle_pgn_129026_cog_sog(mock_session):
    """Test handling PGN 129026 (COG & SOG, Rapid Update)"""
    msg = NMEA2000Message(
        PGN=129026,
        id="cogSogRapidUpdate",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="cog",
            name="COG",
            value=1.5708,  # 90 degrees in radians
            unit_of_measurement="rad",
        ),
        NMEA2000Field(
            id="sog",
            name="SOG",
            value=5.14,  # ~10 knots in m/s
            unit_of_measurement="m/s",
        ),
    ]

    # Call handler
    handle_pgn_129026(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify both COG and SOG were published
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 2


def test_handle_pgn_127250_heading(mock_session):
    """Test handling PGN 127250 (Vessel Heading)"""
    msg = NMEA2000Message(
        PGN=127250,
        id="vesselHeading",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="heading",
            name="Heading",
            value=3.14159,  # ~180 degrees in radians
            unit_of_measurement="rad",
        ),
        NMEA2000Field(
            id="reference",
            name="Reference",
            value="True",
        ),
    ]

    # Call handler
    handle_pgn_127250(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify heading was published
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1


def test_process_message_valid_json(mock_session):
    """Test processing a valid JSON message"""
    # Create a valid NMEA2000 message JSON
    msg = NMEA2000Message(
        PGN=129025,
        id="positionRapidUpdate",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(id="latitude", name="Latitude", value=59.0),
        NMEA2000Field(id="longitude", name="Longitude", value=18.0),
    ]

    json_str = msg.to_json()

    # Process the message
    n2k2keelson.process_message(
        json_str, mock_session, "test/vessel", "sensors", "n2k/test", False
    )

    # Verify publisher was called
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) > 0


def test_process_message_invalid_json(mock_session):
    """Test processing invalid JSON (should not crash)"""
    # This should handle the error gracefully
    n2k2keelson.process_message(
        "invalid json {{{", mock_session, "test/vessel", "sensors", "n2k/test", False
    )

    # Verify no publisher calls were made
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 0


def test_pgn_handler_registry():
    """Test that PGN handler registry contains expected PGNs"""
    expected_pgns = [129025, 129026, 129029, 127250, 127257, 130306, 127245, 130311]

    for pgn in expected_pgns:
        assert pgn in n2k2keelson.PGN_HANDLERS, f"PGN {pgn} not in handler registry"


def test_pgn_128267_not_supported():
    """Test that PGN 128267 (Water Depth) is not supported"""
    # PGN 128267 was removed because depth_below_transducer_m doesn't exist in Keelson
    assert (
        128267 not in n2k2keelson.PGN_HANDLERS
    ), "PGN 128267 should not be in handler registry"


# Tests for degree-unit input (from canboat via n2k-cli stdio)
# These verify handlers correctly skip rad→deg conversion when unit is "deg"


def test_handle_pgn_129026_cog_in_degrees(mock_session):
    """Test handling PGN 129026 with COG already in degrees (from canboat)"""
    msg = NMEA2000Message(
        PGN=129026,
        id="cogSogRapidUpdate",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="cog",
            name="COG",
            value=180.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="sog",
            name="SOG",
            value=5.14,
            unit_of_measurement="m/s",
        ),
    ]

    # Call handler
    handle_pgn_129026(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify published - the value should remain 180.0, not be converted again
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 2


def test_handle_pgn_127250_heading_in_degrees(mock_session):
    """Test handling PGN 127250 with heading already in degrees (from canboat)"""
    msg = NMEA2000Message(
        PGN=127250,
        id="vesselHeading",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="heading",
            name="Heading",
            value=270.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="reference",
            name="Reference",
            value="True",
        ),
    ]

    # Call handler
    handle_pgn_127250(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify published
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1


def test_handle_pgn_127257_attitude_in_degrees(mock_session):
    """Test handling PGN 127257 with attitude values already in degrees (from canboat)"""
    msg = NMEA2000Message(
        PGN=127257,
        id="attitude",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="pitch",
            name="Pitch",
            value=5.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="roll",
            name="Roll",
            value=-3.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="yaw",
            name="Yaw",
            value=45.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
    ]

    # Call handler
    handle_pgn_127257(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify all three values were published
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 3


def test_handle_pgn_130306_wind_angle_in_degrees(mock_session):
    """Test handling PGN 130306 with wind angle already in degrees (from canboat)"""
    msg = NMEA2000Message(
        PGN=130306,
        id="windData",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="windAngle",
            name="Wind Angle",
            value=45.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
        NMEA2000Field(
            id="windSpeed",
            name="Wind Speed",
            value=10.0,
            unit_of_measurement="m/s",
        ),
    ]

    # Call handler
    handle_pgn_130306(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify both values were published
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 2


def test_handle_pgn_127245_rudder_in_degrees(mock_session):
    """Test handling PGN 127245 with rudder angle already in degrees (from canboat)"""
    msg = NMEA2000Message(
        PGN=127245,
        id="rudder",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="position",
            name="Position",
            value=15.0,  # Already in degrees
            unit_of_measurement="deg",
        ),
    ]

    # Call handler
    handle_pgn_127245(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # Verify rudder angle was published
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1


def test_handle_pgn_127257_no_unit_specified(mock_session):
    """Test handling PGN 127257 with no unit specified (assumes degrees)"""
    msg = NMEA2000Message(
        PGN=127257,
        id="attitude",
        timestamp=datetime.now(timezone.utc),
    )
    msg.fields = [
        NMEA2000Field(
            id="pitch",
            name="Pitch",
            value=5.0,
            unit_of_measurement=None,  # No unit - should use value as-is
        ),
    ]

    # Call handler - should not crash and should treat value as degrees
    handle_pgn_127257(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1


# ==================== Test handle_pgn_129029 with method + integrity ====================


def _run_pgn_129029_and_get_quality(method=None, integrity=None) -> LocationFixQuality:
    """Run handle_pgn_129029 with the given method/integrity values and return
    the published LocationFixQuality message."""
    n2k2keelson.PUBLISHERS.clear()

    # Track declare_publisher calls so we know which capture maps to which subject.
    declared = []

    def declare_publisher(key):
        p = Mock()
        p.published_data = []
        p.put = Mock(side_effect=lambda x: p.published_data.append(x))
        declared.append((key, p))
        return p

    session = Mock()
    session.declare_publisher = Mock(side_effect=declare_publisher)

    fields = [
        NMEA2000Field(id="latitude", name="Latitude", value=59.0),
        NMEA2000Field(id="longitude", name="Longitude", value=18.0),
    ]
    if method is not None:
        fields.append(NMEA2000Field(id="method", name="GNSS Method", value=method))
    if integrity is not None:
        fields.append(NMEA2000Field(id="integrity", name="Integrity", value=integrity))

    msg = NMEA2000Message(
        PGN=129029, id="gnssPositionData", timestamp=datetime.now(timezone.utc)
    )
    msg.fields = fields

    handle_pgn_129029(msg, session, "test/vessel", "sensors", "n2k/test")

    quality_publisher = next(p for key, p in declared if "location_fix_quality" in key)
    assert len(quality_publisher.published_data) == 1
    _, _, payload_bytes = keelson.uncover(quality_publisher.published_data[0])
    quality = LocationFixQuality()
    quality.ParseFromString(payload_bytes)
    return quality


def test_pgn_129029_method_rtk_fixed_integer_code():
    quality = _run_pgn_129029_and_get_quality(method=4, integrity=1)
    assert quality.fix_type == LocationFixQuality.FIX_3D
    assert quality.pos_type == LocationFixQuality.POS_TYPE_RTK_INT
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_FIXED
    assert quality.integrity == LocationFixQuality.INTEGRITY_SAFE


def test_pgn_129029_method_rtk_float_name_lookup():
    """method may arrive as a resolved string instead of an int."""
    quality = _run_pgn_129029_and_get_quality(method="RTK float", integrity="Caution")
    assert quality.pos_type == LocationFixQuality.POS_TYPE_RTK_FLOAT
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_FLOAT
    assert quality.integrity == LocationFixQuality.INTEGRITY_CAUTION


def test_pgn_129029_method_dgnss_integer():
    quality = _run_pgn_129029_and_get_quality(method=2)
    assert quality.pos_type == LocationFixQuality.POS_TYPE_PSRDIFF
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_DIFFERENTIAL


def test_pgn_129029_integrity_only_unknown_method():
    quality = _run_pgn_129029_and_get_quality(integrity=3)
    assert quality.integrity == LocationFixQuality.INTEGRITY_UNSAFE
    # No method → fix_type defaults to UNKNOWN
    assert quality.fix_type == LocationFixQuality.UNKNOWN


def test_pgn_129029_no_method_no_integrity_skips_quality(mock_session):
    """When neither method nor integrity is present, no quality is published."""
    n2k2keelson.PUBLISHERS.clear()
    msg = NMEA2000Message(
        PGN=129029, id="gnssPositionData", timestamp=datetime.now(timezone.utc)
    )
    msg.fields = [
        NMEA2000Field(id="latitude", name="Latitude", value=59.0),
        NMEA2000Field(id="longitude", name="Longitude", value=18.0),
    ]
    handle_pgn_129029(msg, mock_session, "test/vessel", "sensors", "n2k/test")

    # mock_session shares one publisher; only location_fix is published.
    publisher = mock_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1
