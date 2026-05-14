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
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality

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

    # Decode location_fix (second published item, after quality)
    location_data = publisher.published_data[1]
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

    # Should publish: location_fix, satellites_used, hdop, undulation, fix_quality
    assert len(published_data) == 5

    # Check quality (first item) - GGA quality 1 = FIX_3D
    _, _, qual_bytes = keelson.uncover(published_data[0])
    qual_payload = LocationFixQuality()
    qual_payload.ParseFromString(qual_bytes)
    assert qual_payload.fix_type == LocationFixQuality.FIX_3D

    # Check satellites (third item)
    _, _, sats_bytes = keelson.uncover(published_data[2])
    sats_payload = TimestampedInt()
    sats_payload.ParseFromString(sats_bytes)
    assert sats_payload.value == 8

    # Check HDOP (fourth item)
    _, _, hdop_bytes = keelson.uncover(published_data[3])
    hdop_payload = TimestampedFloat()
    hdop_payload.ParseFromString(hdop_bytes)
    assert abs(hdop_payload.value - 0.9) < 0.001


def test_handle_gga_altitude():
    """Test GGA handler publishes altitude in LocationFix."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
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

    handle_gga(msg, session, args)

    # Decode location_fix (second published item, after quality)
    _, _, payload_bytes = keelson.uncover(published_data[1])
    location = LocationFix()
    location.ParseFromString(payload_bytes)

    assert abs(location.altitude - 545.4) < 0.01


def test_handle_gga_quality_pps():
    """Test GGA quality mapping for PPS fix (quality=3)."""
    nmea01832keelson.PUBLISHERS.clear()

    msg = pynmea2.GGA(
        "GP",
        "GGA",
        (
            "123519",
            "4807.038",
            "N",
            "01131.000",
            "E",
            "3",
            "08",
            "0.9",
            "545.4",
            "M",
            "46.9",
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

    # First item is quality
    _, _, qual_bytes = keelson.uncover(published_data[0])
    qual_payload = LocationFixQuality()
    qual_payload.ParseFromString(qual_bytes)
    assert qual_payload.fix_type == LocationFixQuality.FIX_3D


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

    # Should publish location_fix and location_fix_quality (gps_qual="1")
    assert len(published_data) == 2


# ==================== Test handle_gga LocationFixQuality publishing ====================


def _run_gga_and_extract_quality(gps_qual: str) -> LocationFixQuality:
    """Feed a GGA sentence with a given gps_qual digit and return the published
    LocationFixQuality message."""
    nmea01832keelson.PUBLISHERS.clear()

    msg = pynmea2.GGA(
        "GP",
        "GGA",
        (
            "123519",
            "4807.038",
            "N",
            "01131.000",
            "E",
            gps_qual,
            "08",
            "0.9",
            "545.4",
            "M",
            "46.9",
            "M",
            "",
            "0000",
        ),
    )

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))
    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)
    declare_calls = []
    session.declare_publisher.side_effect = lambda key: (
        declare_calls.append(key) or publisher
    )

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(msg, session, args)

    # Find the index of the location_fix_quality publish using the declare order.
    quality_index = next(
        i for i, key in enumerate(declare_calls) if "location_fix_quality" in key
    )
    _, _, payload_bytes = keelson.uncover(published_data[quality_index])
    quality = LocationFixQuality()
    quality.ParseFromString(payload_bytes)
    return quality


def test_gga_quality_publishes_single_fix():
    quality = _run_gga_and_extract_quality("1")
    assert quality.fix_type == LocationFixQuality.FIX_3D
    assert quality.pos_type == LocationFixQuality.POS_TYPE_SINGLE
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_NONE
    assert quality.integrity == LocationFixQuality.INTEGRITY_UNKNOWN


def test_gga_quality_publishes_dgps():
    quality = _run_gga_and_extract_quality("2")
    assert quality.fix_type == LocationFixQuality.FIX_3D
    assert quality.pos_type == LocationFixQuality.POS_TYPE_PSRDIFF
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_DIFFERENTIAL


def test_gga_quality_publishes_rtk_fixed():
    quality = _run_gga_and_extract_quality("4")
    assert quality.fix_type == LocationFixQuality.FIX_3D
    assert quality.pos_type == LocationFixQuality.POS_TYPE_RTK_INT
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_FIXED


def test_gga_quality_publishes_rtk_float():
    quality = _run_gga_and_extract_quality("5")
    assert quality.fix_type == LocationFixQuality.FIX_3D
    assert quality.pos_type == LocationFixQuality.POS_TYPE_RTK_FLOAT
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_FLOAT


def test_gga_quality_publishes_invalid():
    quality = _run_gga_and_extract_quality("0")
    assert quality.fix_type == LocationFixQuality.INVALID
    assert quality.pos_type == LocationFixQuality.POS_TYPE_NO_SOLUTION


def test_gga_quality_publishes_dr():
    quality = _run_gga_and_extract_quality("6")
    assert quality.fix_type == LocationFixQuality.DR_ONLY


def test_gga_quality_unknown_digit_falls_through_to_defaults():
    """An out-of-range gps_qual integer should publish a quality message with
    the UNKNOWN/POS_TYPE_UNKNOWN/RTK_STATUS_NONE fallback."""
    quality = _run_gga_and_extract_quality("9")
    assert quality.fix_type == LocationFixQuality.UNKNOWN
    assert quality.pos_type == LocationFixQuality.POS_TYPE_UNKNOWN
    assert quality.rtk_status == LocationFixQuality.RTK_STATUS_NONE


def test_gga_quality_unparseable_digit_skips_quality_publish():
    """A non-numeric gps_qual must hit the except branch and skip the
    location_fix_quality publish entirely (other fields still publish)."""
    nmea01832keelson.PUBLISHERS.clear()

    msg = pynmea2.GGA(
        "GP",
        "GGA",
        (
            "123519",
            "4807.038",
            "N",
            "01131.000",
            "E",
            "X",  # unparseable
            "08",
            "0.9",
            "545.4",
            "M",
            "46.9",
            "M",
            "",
            "0000",
        ),
    )

    publisher = Mock()
    publisher.put = Mock()
    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)
    declare_calls = []
    session.declare_publisher.side_effect = lambda key: (
        declare_calls.append(key) or publisher
    )

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(msg, session, args)

    # No publisher should have been declared for location_fix_quality.
    assert not any("location_fix_quality" in key for key in declare_calls)


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

    # Wind directions: true and magnetic share the true_wind_direction_deg
    # subject; magnetic is distinguished by a /magnetic source_id suffix.
    key_exprs = [call.args[0] for call in session.declare_publisher.call_args_list]
    assert not any(
        "wind_direction_magnetic_deg" in k for k in key_exprs
    ), "wind_direction_magnetic_deg subject was removed and must not be published"
    true_keys = [
        k
        for k in key_exprs
        if "true_wind_direction_deg" in k and not k.endswith("/magnetic")
    ]
    magnetic_keys = [
        k
        for k in key_exprs
        if k.endswith("/true_wind_direction_deg/weather/test/MDA/magnetic")
    ]
    assert true_keys, f"Expected a true wind direction key, got: {key_exprs}"
    assert (
        magnetic_keys
    ), f"Expected a magnetic wind direction key with /magnetic suffix, got: {key_exprs}"
    assert any(
        k.endswith("/true_wind_direction_deg/weather/test/MDA") for k in true_keys
    ), f"True wind direction key should end in /MDA, got: {true_keys}"


def test_handle_mda_partial():
    """Test MDA handler with subset of fields."""
    nmea01832keelson.PUBLISHERS.clear()

    # Create a minimal MDA message with just pressure
    msg = Mock()
    msg.sentence_type = "MDA"
    msg.b_pressure_bar = "1.013"
    msg.i_pressure_inch = None
    msg.air_temp = "22.5"
    msg.water_temp = None
    msg.rel_humidity = None
    msg.dew_point = None
    msg.direction_true = None
    msg.direction_magnetic = None
    msg.wind_speed_meters = None
    msg.wind_speed_knots = None

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


# ==================== Test sentence_type in key expression ====================


def test_sentence_type_appended_to_source_id():
    """Test that NMEA sentence type is appended to source_id in publisher key."""
    nmea01832keelson.PUBLISHERS.clear()

    nmea_sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    msg = pynmea2.parse(nmea_sentence)

    publisher = Mock()
    publisher.put = Mock()

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(msg, session, args)

    # All publisher keys should contain the sentence type suffix
    for call in session.declare_publisher.call_args_list:
        key_expr = call[0][0]
        assert key_expr.endswith(
            "/gps/test/GGA"
        ), f"Expected key to end with /gps/test/GGA, got: {key_expr}"


def test_different_sentences_produce_different_keys():
    """Test that GGA and RMC produce different keys for location_fix."""
    nmea01832keelson.PUBLISHERS.clear()

    gga_msg = pynmea2.parse(
        "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    )
    rmc_msg = pynmea2.parse(
        "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    )

    publisher = Mock()
    publisher.put = Mock()

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gps/test"

    handle_gga(gga_msg, session, args)
    handle_rmc(rmc_msg, session, args)

    # Collect all key expressions used
    key_exprs = [call[0][0] for call in session.declare_publisher.call_args_list]

    # Find location_fix keys (exclude quality, hdop, satellites, undulation)
    location_keys = [
        k
        for k in key_exprs
        if "location_fix" in k
        and "hdop" not in k
        and "satellites" not in k
        and "undulation" not in k
        and "quality" not in k
    ]
    assert len(location_keys) == 2
    assert any(k.endswith("/gps/test/GGA") for k in location_keys)
    assert any(k.endswith("/gps/test/RMC") for k in location_keys)


# --- UNIHEADINGA tests ---

SAMPLE_UNIHEADINGA = (
    "#UNIHEADINGA,94,GPS,FINE,2410,476441000,0,0,18,31;"
    "SOL_COMPUTED,NARROW_INT,1.0049,247.7241,0.3985,0.0000,"
    '0.8572,1.0330,"999",32,23,23,19,3,01,3,f3*debdfb39'
)

SAMPLES_FILE = (
    pathlib.Path(__file__).resolve().parent / "data" / "UNIHEADING_samples.txt"
)

parse_uniheadinga = nmea01832keelson.parse_uniheadinga
handle_uniheadinga = nmea01832keelson.handle_uniheadinga


def test_parse_uniheadinga():
    """Test parsing a single UNIHEADINGA sentence."""
    result = parse_uniheadinga(SAMPLE_UNIHEADINGA)
    assert result["solution_status"] == "SOL_COMPUTED"
    assert abs(result["heading"] - 247.7241) < 0.0001
    assert abs(result["pitch"] - 0.3985) < 0.0001


def test_parse_uniheadinga_all_samples():
    """Test parsing all 65 sample lines from the data file."""
    lines = SAMPLES_FILE.read_text().strip().splitlines()
    assert len(lines) == 65

    for i, line in enumerate(lines):
        result = parse_uniheadinga(line)
        assert (
            result["solution_status"] == "SOL_COMPUTED"
        ), f"Line {i}: unexpected status"
        assert 246.0 < result["heading"] < 248.0, f"Line {i}: heading out of range"
        assert -5.0 < result["pitch"] < 5.0, f"Line {i}: pitch out of range"


def test_handle_uniheadinga_publishes_heading_and_pitch():
    """Test that handle_uniheadinga publishes both heading and pitch."""
    nmea01832keelson.PUBLISHERS.clear()

    fields = parse_uniheadinga(SAMPLE_UNIHEADINGA)

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gnss/test"

    handle_uniheadinga(fields, session, args)

    assert len(published_data) == 2

    # First publication: heading_true_north_deg
    _, _, heading_bytes = keelson.uncover(published_data[0])
    heading_payload = TimestampedFloat()
    heading_payload.ParseFromString(heading_bytes)
    assert abs(heading_payload.value - 247.7241) < 0.0001

    # Second publication: pitch_deg
    _, _, pitch_bytes = keelson.uncover(published_data[1])
    pitch_payload = TimestampedFloat()
    pitch_payload.ParseFromString(pitch_bytes)
    assert abs(pitch_payload.value - 0.3985) < 0.0001


def test_handle_uniheadinga_skips_non_sol_computed():
    """Test that handle_uniheadinga does not publish when solution is not SOL_COMPUTED."""
    nmea01832keelson.PUBLISHERS.clear()

    fields = {
        "solution_status": "INSUFFICIENT_OBS",
        "heading": 247.0,
        "pitch": 0.5,
    }

    publisher = Mock()
    published_data = []
    publisher.put = Mock(side_effect=lambda x: published_data.append(x))

    session = Mock()
    session.declare_publisher = Mock(return_value=publisher)

    args = Mock()
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.source_id = "gnss/test"

    handle_uniheadinga(fields, session, args)

    assert len(published_data) == 0


def test_parse_uniheadinga_malformed():
    """Test that parse_uniheadinga raises ValueError on malformed input."""
    import pytest

    # No semicolon
    with pytest.raises(ValueError):
        parse_uniheadinga("#UNIHEADINGA,no,semicolon,here")

    # Too few body fields
    with pytest.raises((ValueError, IndexError)):
        parse_uniheadinga("#UNIHEADINGA,header;field1,field2")
