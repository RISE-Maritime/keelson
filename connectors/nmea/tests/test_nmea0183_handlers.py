#!/usr/bin/env python3

"""Tests for nmea01832keelson - NMEA parsing and publishing."""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
from datetime import datetime, timezone, time as dt_time, date
from unittest.mock import Mock
import pynmea2
import keelson
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
    TimestampedTimestamp,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))  # Make sibling imports work

# Import the script dynamically
script_path = bin_root / "nmea01832keelson.py"
loader = SourceFileLoader("nmea01832keelson", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
nmea01832keelson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nmea01832keelson)

# Import functions to test
nmea_time_to_nanoseconds = nmea01832keelson.nmea_time_to_nanoseconds
handle_gga = nmea01832keelson.handle_gga
handle_rmc = nmea01832keelson.handle_rmc
handle_hdt = nmea01832keelson.handle_hdt
handle_hdg = nmea01832keelson.handle_hdg
handle_hdm = nmea01832keelson.handle_hdm
handle_vtg = nmea01832keelson.handle_vtg
handle_zda = nmea01832keelson.handle_zda
handle_gll = nmea01832keelson.handle_gll
handle_rot = nmea01832keelson.handle_rot
handle_gsa = nmea01832keelson.handle_gsa
handle_mda = nmea01832keelson.handle_mda


# ==================== Test nmea_time_to_nanoseconds ====================


def test_nmea_time_to_nanoseconds_with_date_and_time():
    """Test conversion with both date and time."""
    test_date = date(2024, 1, 15)
    test_time = dt_time(12, 30, 45, 500000)  # 12:30:45.5

    result = nmea_time_to_nanoseconds(test_date, test_time)

    expected = datetime(2024, 1, 15, 12, 30, 45, 500000, tzinfo=timezone.utc)
    expected_ns = int(expected.timestamp() * 1_000_000_000)

    assert result == expected_ns


def test_nmea_time_to_nanoseconds_with_time_only():
    """Test conversion with time only (uses today's date)."""
    test_time = dt_time(12, 30, 45)

    result = nmea_time_to_nanoseconds(None, test_time)

    # Should use today's date
    today = datetime.now(timezone.utc).date()
    expected = datetime.combine(today, test_time, tzinfo=timezone.utc)
    expected_ns = int(expected.timestamp() * 1_000_000_000)

    assert result == expected_ns


def test_nmea_time_to_nanoseconds_with_none():
    """Test conversion with None returns None."""
    assert nmea_time_to_nanoseconds(None, None) is None


# ==================== Test handle_gga ====================


def test_handle_gga_complete(mock_zenoh_session):
    """Test GGA handler with complete data."""
    nmea_sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    msg = pynmea2.parse(nmea_sentence)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(msg, mock_zenoh_session, args)

    # Should have called declare_publisher multiple times
    assert mock_zenoh_session.declare_publisher.call_count >= 1

    # Get the publisher and check published data
    publisher = mock_zenoh_session.declare_publisher.return_value
    assert len(publisher.published_data) >= 1

    # Decode location_fix (first published item)
    location_data = publisher.published_data[0]
    _, _, payload_bytes = keelson.uncover(location_data)
    location = LocationFix()
    location.ParseFromString(payload_bytes)

    # Verify position (48°07.038'N = 48.1173°, 11°31.000'E = 11.5167°)
    assert abs(location.latitude - 48.1173) < 0.001
    assert abs(location.longitude - 11.5167) < 0.001


def test_handle_gga_with_satellites_and_hdop():
    """Test GGA handler publishes satellite count and HDOP."""
    # Clear the PUBLISHERS cache before test
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    msg = pynmea2.parse(nmea_sentence)

    # Create a mock publisher that captures all put() calls
    publisher = Mock()
    published_data = []

    def mock_put(data):
        published_data.append(data)

    publisher.put = Mock(side_effect=mock_put)

    # Create a mock session that always returns the same publisher
    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(msg, session, args)

    # Should publish: location_fix, satellites_used, hdop, undulation
    assert len(published_data) == 4

    # Check satellites (second item)
    _, _, sats_bytes = keelson.uncover(published_data[1])
    sats_payload = TimestampedInt()
    sats_payload.ParseFromString(sats_bytes)
    assert sats_payload.value == 8

    # Check HDOP (third item)
    _, _, hdop_bytes = keelson.uncover(published_data[2])
    hdop_payload = TimestampedFloat()
    hdop_payload.ParseFromString(hdop_bytes)
    assert abs(hdop_payload.value - 0.9) < 0.001


def test_handle_gga_minimal():
    """Test GGA handler with minimal data (position only)."""
    nmea01832keelson.PUBLISHERS.clear()

    # GGA with only position, no satellites/HDOP
    msg = pynmea2.GGA(
        "GP",
        "GGA",
        (
            "123519",
            "4807.038",
            "N",
            "01131.000",
            "E",
            "1",
            "",
            "",
            "",
            "M",
            "",
            "M",
            "",
            "",
        ),
    )

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(msg, session, args)

    # Should only publish location_fix
    assert len(published_data) == 1


# ==================== Test handle_rmc ====================


def test_handle_rmc_complete():
    """Test RMC handler with complete data."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = (
        "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    )
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_rmc(msg, session, args)

    # Should publish: location_fix, speed, course
    assert len(published_data) == 3

    # Check speed (second item)
    _, _, speed_bytes = keelson.uncover(published_data[1])
    speed_payload = TimestampedFloat()
    speed_payload.ParseFromString(speed_bytes)
    assert abs(speed_payload.value - 22.4) < 0.001

    # Check course (third item)
    _, _, course_bytes = keelson.uncover(published_data[2])
    course_payload = TimestampedFloat()
    course_payload.ParseFromString(course_bytes)
    assert abs(course_payload.value - 84.4) < 0.001


def test_handle_rmc_invalid_status():
    """Test RMC handler ignores position when status is invalid."""
    nmea01832keelson.PUBLISHERS.clear()

    # Status 'V' = invalid
    msg = pynmea2.RMC(
        "GP",
        "RMC",
        (
            "123519",
            "V",
            "4807.038",
            "N",
            "01131.000",
            "E",
            "022.4",
            "084.4",
            "230394",
            "003.1",
            "W",
            "A",
        ),
    )

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_rmc(msg, session, args)

    # Should only publish speed and course, not location
    assert len(published_data) == 2


# ==================== Test handle_hdt ====================


def test_handle_hdt():
    """Test HDT handler."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$HEHDT,274.5,T*2B"
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "compass/test"

    handle_hdt(msg, session, args)

    assert len(published_data) == 1

    _, _, heading_bytes = keelson.uncover(published_data[0])
    heading_payload = TimestampedFloat()
    heading_payload.ParseFromString(heading_bytes)
    assert abs(heading_payload.value - 274.5) < 0.001


# ==================== Test handle_vtg ====================


def test_handle_vtg():
    """Test VTG handler."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$GPVTG,054.7,T,034.4,M,005.5,N,010.2,K*48"
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_vtg(msg, session, args)

    # Should publish course and speed
    assert len(published_data) == 2

    # Check course
    _, _, course_bytes = keelson.uncover(published_data[0])
    course_payload = TimestampedFloat()
    course_payload.ParseFromString(course_bytes)
    assert abs(course_payload.value - 54.7) < 0.001

    # Check speed
    _, _, speed_bytes = keelson.uncover(published_data[1])
    speed_payload = TimestampedFloat()
    speed_payload.ParseFromString(speed_bytes)
    assert abs(speed_payload.value - 5.5) < 0.001


# ==================== Test handle_zda ====================


def test_handle_zda(mock_zenoh_session):
    """Test ZDA handler."""
    msg = pynmea2.ZDA("GP", "ZDA", ("123519.50", "23", "03", "1994", "00", "00"))

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_zda(msg, mock_zenoh_session, args)

    publisher = mock_zenoh_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1

    _, _, timestamp_bytes = keelson.uncover(publisher.published_data[0])
    timestamp_payload = TimestampedTimestamp()
    timestamp_payload.ParseFromString(timestamp_bytes)

    # Verify it's the correct timestamp
    expected = datetime(1994, 3, 23, 12, 35, 19, 500000, tzinfo=timezone.utc)
    expected_ns = int(expected.timestamp() * 1_000_000_000)
    assert timestamp_payload.value.ToNanoseconds() == expected_ns


# ==================== Test handle_gll ====================


def test_handle_gll_valid():
    """Test GLL handler with valid status."""
    nmea01832keelson.PUBLISHERS.clear()

    msg = pynmea2.GLL(
        "GP", "GLL", ("4807.038", "N", "01131.000", "E", "123519", "A", "A")
    )

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gll(msg, session, args)

    assert len(published_data) == 1

    _, _, location_bytes = keelson.uncover(published_data[0])
    location = LocationFix()
    location.ParseFromString(location_bytes)
    assert abs(location.latitude - 48.1173) < 0.001


def test_handle_gll_invalid(mock_zenoh_session):
    """Test GLL handler ignores invalid status."""
    msg = pynmea2.GLL(
        "GP", "GLL", ("4807.038", "N", "01131.000", "E", "123519", "V", "A")
    )

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gll(msg, mock_zenoh_session, args)

    publisher = mock_zenoh_session.declare_publisher.return_value
    assert len(publisher.published_data) == 0


# ==================== Test handle_rot ====================


def test_handle_rot(mock_zenoh_session):
    """Test ROT handler converts degrees/minute to degrees/second."""
    msg = pynmea2.ROT("HE", "ROT", ("3.5", "A"))

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gyro/test"

    handle_rot(msg, mock_zenoh_session, args)

    publisher = mock_zenoh_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1

    _, _, rot_bytes = keelson.uncover(publisher.published_data[0])
    rot_payload = TimestampedFloat()
    rot_payload.ParseFromString(rot_bytes)

    # 3.5 deg/min = 3.5/60 deg/sec
    assert abs(rot_payload.value - (3.5 / 60.0)) < 0.0001


# ==================== Test handle_gsa ====================


def test_handle_gsa():
    """Test GSA handler publishes all DOP values."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$GPGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*39"
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gsa(msg, session, args)

    # Should publish HDOP, VDOP, PDOP
    assert len(published_data) == 3

    # Check HDOP
    _, _, hdop_bytes = keelson.uncover(published_data[0])
    hdop = TimestampedFloat()
    hdop.ParseFromString(hdop_bytes)
    assert abs(hdop.value - 1.3) < 0.001

    # Check VDOP
    _, _, vdop_bytes = keelson.uncover(published_data[1])
    vdop = TimestampedFloat()
    vdop.ParseFromString(vdop_bytes)
    assert abs(vdop.value - 2.1) < 0.001

    # Check PDOP
    _, _, pdop_bytes = keelson.uncover(published_data[2])
    pdop = TimestampedFloat()
    pdop.ParseFromString(pdop_bytes)
    assert abs(pdop.value - 2.5) < 0.001


# ==================== Test handle_hdg ====================


def test_handle_hdg_full():
    """Test HDG handler with heading, deviation, and variation."""
    nmea01832keelson.PUBLISHERS.clear()

    # HDG sentence: heading 98.3, deviation 0.0 E, variation 12.6 W
    nmea_sentence = "$HCHDG,98.3,0.0,E,12.6,W*57"
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "compass/test"

    handle_hdg(msg, session, args)

    # Should publish heading, deviation, and variation
    assert len(published_data) == 3

    # Check heading
    _, _, heading_bytes = keelson.uncover(published_data[0])
    heading_payload = TimestampedFloat()
    heading_payload.ParseFromString(heading_bytes)
    assert abs(heading_payload.value - 98.3) < 0.001

    # Check deviation (E = positive, so 0.0)
    _, _, dev_bytes = keelson.uncover(published_data[1])
    dev_payload = TimestampedFloat()
    dev_payload.ParseFromString(dev_bytes)
    assert abs(dev_payload.value - 0.0) < 0.001

    # Check variation (W = negative, so -12.6)
    _, _, var_bytes = keelson.uncover(published_data[2])
    var_payload = TimestampedFloat()
    var_payload.ParseFromString(var_bytes)
    assert abs(var_payload.value - (-12.6)) < 0.001


def test_handle_hdg_heading_only():
    """Test HDG handler with just heading."""
    nmea01832keelson.PUBLISHERS.clear()

    # Create HDG with only heading
    msg = pynmea2.HDG("HC", "HDG", ("98.3", "", "", "", ""))

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "compass/test"

    handle_hdg(msg, session, args)

    # Should only publish heading
    assert len(published_data) == 1

    _, _, heading_bytes = keelson.uncover(published_data[0])
    heading_payload = TimestampedFloat()
    heading_payload.ParseFromString(heading_bytes)
    assert abs(heading_payload.value - 98.3) < 0.001


# ==================== Test handle_hdm ====================


def test_handle_hdm():
    """Test HDM handler."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$HCHDM,98.3,M*1B"
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "compass/test"

    handle_hdm(msg, session, args)

    assert len(published_data) == 1

    _, _, heading_bytes = keelson.uncover(published_data[0])
    heading_payload = TimestampedFloat()
    heading_payload.ParseFromString(heading_bytes)
    assert abs(heading_payload.value - 98.3) < 0.001


# ==================== Test handle_mda ====================


def test_handle_mda_full():
    """Test MDA handler with all fields."""
    nmea01832keelson.PUBLISHERS.clear()

    # MDA with pressure (bars), air temp, water temp, humidity, dew point,
    # wind direction (true/magnetic), wind speed (knots/m/s)
    nmea_sentence = (
        "$WIMDA,29.7,I,1.006,B,25.4,C,,,45.2,,12.5,C,130.0,T,125.0,M,5.2,N,2.7,M*62"
    )
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "weather/test"

    handle_mda(msg, session, args)

    # Should publish multiple values
    assert len(published_data) >= 1

    # Find and check pressure (bars to Pascals: 1.006 * 100000 = 100600)
    found_pressure = False
    for data in published_data:
        _, _, payload_bytes = keelson.uncover(data)
        payload = TimestampedFloat()
        payload.ParseFromString(payload_bytes)
        if abs(payload.value - 100600.0) < 100:  # Allow some tolerance
            found_pressure = True
            break
    assert found_pressure, "Expected air pressure in Pascals"


def test_handle_mda_partial():
    """Test MDA handler with subset of fields."""
    nmea01832keelson.PUBLISHERS.clear()

    # Create a minimal MDA message with just pressure
    msg = Mock()
    msg.b_pressure_bar = "1.013"
    msg.i_pressure_inch = None
    msg.air_temp = "22.5"
    msg.water_temp = None
    msg.rel_humidity = None
    msg.dew_point = None
    msg.direction_true = None
    msg.direction_mag = None
    msg.wind_speed_ms = None
    msg.wind_speed_kn = None

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "weather/test"

    handle_mda(msg, session, args)

    # Should publish pressure and air temp only
    assert len(published_data) == 2

    # Check pressure (1.013 bars = 101300 Pa)
    _, _, pressure_bytes = keelson.uncover(published_data[0])
    pressure_payload = TimestampedFloat()
    pressure_payload.ParseFromString(pressure_bytes)
    assert abs(pressure_payload.value - 101300.0) < 10

    # Check air temperature
    _, _, temp_bytes = keelson.uncover(published_data[1])
    temp_payload = TimestampedFloat()
    temp_payload.ParseFromString(temp_bytes)
    assert abs(temp_payload.value - 22.5) < 0.001
