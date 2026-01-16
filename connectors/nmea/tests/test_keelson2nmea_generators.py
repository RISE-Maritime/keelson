#!/usr/bin/env python3

"""Tests for keelson2nmea0183 - NMEA generation from Keelson data."""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
import io
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
import pytest
import pynmea2
import skarv
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedInt
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))

# Import the script dynamically
script_path = bin_root / "keelson2nmea0183.py"
loader = SourceFileLoader("keelson2nmea0183", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
keelson2nmea0183 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keelson2nmea0183)

# Import functions to test
format_lat_lon = keelson2nmea0183.format_lat_lon
unpack = keelson2nmea0183.unpack
generate_gga = keelson2nmea0183.generate_gga
generate_rmc = keelson2nmea0183.generate_rmc
generate_gll = keelson2nmea0183.generate_gll
generate_vtg = keelson2nmea0183.generate_vtg
generate_hdt = keelson2nmea0183.generate_hdt
generate_rot = keelson2nmea0183.generate_rot
generate_gsa = keelson2nmea0183.generate_gsa
generate_zda = keelson2nmea0183.generate_zda


# ==================== Test format_lat_lon ====================


def test_format_lat_lon_north_east():
    """Test formatting for North/East coordinates."""
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(48.1173, 11.5167)

    assert lat_dir == "N"
    assert lon_dir == "E"

    # 48.1173° = 48°07.038'
    assert lat_str.startswith("48")
    # Minutes should be around 7.038
    lat_min = float(lat_str[2:])
    assert abs(lat_min - 7.038) < 0.001

    # 11.5167° = 11°31.002'
    assert lon_str.startswith("011")
    lon_min = float(lon_str[3:])
    assert abs(lon_min - 31.002) < 0.001


def test_format_lat_lon_south_west():
    """Test formatting for South/West coordinates."""
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(-37.7749, -122.4194)

    assert lat_dir == "S"
    assert lon_dir == "W"

    # Should use absolute values for formatting
    assert lat_str.startswith("37")
    assert lon_str.startswith("122")


def test_format_lat_lon_zero():
    """Test formatting for zero coordinates."""
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(0.0, 0.0)

    assert lat_dir == "N"  # Zero is considered North
    assert lon_dir == "E"  # Zero is considered East


def test_format_lat_lon_formatting():
    """Test string formatting is correct."""
    lat_str, lat_dir, lon_str, lon_dir = format_lat_lon(1.5, 2.5)

    # Latitude should be 2 digits + minutes (ddmm.mmmm)
    assert len(lat_str) == 9  # dd + mm.mmmm

    # Longitude should be 3 digits + minutes (dddmm.mmmm)
    assert len(lon_str) == 10  # ddd + mm.mmmm


# ==================== Helper to create Zenoh samples ====================


def create_skarv_sample(subject: str, payload_bytes: bytes):
    """
    Create a skarv Sample with a Zenoh Payload object.

    skarv.Sample has key_expr and value attributes.
    When mirror() is used, sample.value is a Zenoh Payload object.
    """
    from skarv import Sample

    # Create a mock Zenoh Payload that has to_bytes()
    payload_mock = Mock()
    payload_mock.to_bytes = Mock(return_value=payload_bytes)

    # Create a skarv.Sample with the payload as value
    sample = Sample(subject, payload_mock)

    return sample


def create_zenoh_payload(payload_bytes: bytes):
    """
    Create a zenoh Sample with a Zenoh Payload object.

    zenoh.Sample has key_expr and payload attributes.
    """
    zenoh_payload = MagicMock()
    zenoh_payload.to_bytes = MagicMock(return_value=payload_bytes)
    return zenoh_payload


# ==================== Test unpack ====================


def test_unpack_location_fix():
    """Test unpacking LocationFix message."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(1234567890000000000)
    location.latitude = 48.1173
    location.longitude = 11.5167

    payload_bytes = keelson.enclose(location.SerializeToString())
    sample = create_skarv_sample("location_fix", payload_bytes)

    result = unpack(sample)

    assert abs(result.latitude - 48.1173) < 0.0001
    assert abs(result.longitude - 11.5167) < 0.0001


def test_unpack_timestamped_float():
    """Test unpacking TimestampedFloat message."""
    msg = TimestampedFloat()
    msg.timestamp.FromNanoseconds(1234567890000000000)
    msg.value = 123.45

    payload_bytes = keelson.enclose(msg.SerializeToString())
    sample = create_skarv_sample("speed_over_ground_knots", payload_bytes)

    result = unpack(sample)

    assert abs(result.value - 123.45) < 0.0001


def test_unpack_timestamped_int():
    """Test unpacking TimestampedInt message."""
    msg = TimestampedInt()
    msg.timestamp.FromNanoseconds(1234567890000000000)
    msg.value = 42

    payload_bytes = keelson.enclose(msg.SerializeToString())
    sample = create_skarv_sample("location_fix_satellites_used", payload_bytes)

    result = unpack(sample)

    assert result.value == 42


# ==================== Test NMEA generators ====================


@pytest.fixture
def setup_args():
    """Setup ARGS global for tests."""
    keelson2nmea0183.ARGS = Mock()
    keelson2nmea0183.ARGS.talker_id = "GP"
    yield
    keelson2nmea0183.ARGS = None


def test_generate_gga_complete(setup_args):
    """Test GGA generation with complete data."""
    # Create location sample
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    location.latitude = 48.1173
    location.longitude = 11.5167
    location.altitude = 545.4
    location_payload = keelson.enclose(location.SerializeToString())

    # Create HDOP sample
    hdop = TimestampedFloat()
    hdop.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    hdop.value = 0.9
    hdop_payload = keelson.enclose(hdop.SerializeToString())

    # Create satellites sample
    sats = TimestampedInt()
    sats.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    sats.value = 8
    sats_payload = keelson.enclose(sats.SerializeToString())

    # Create undulation sample
    undulation = TimestampedFloat()
    undulation.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    undulation.value = 46.9
    undulation_payload = keelson.enclose(undulation.SerializeToString())

    # Put samples in skarv
    skarv.put("location_fix", create_zenoh_payload(location_payload))
    skarv.put("location_fix_hdop", create_zenoh_payload(hdop_payload))
    skarv.put("location_fix_satellites_used", create_zenoh_payload(sats_payload))
    skarv.put("location_fix_undulation_m", create_zenoh_payload(undulation_payload))

    # Capture stdout
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_gga()

    # Parse the output
    output = captured_output.getvalue().strip()
    assert output.startswith("$GPGGA")

    # Parse with pynmea2 to verify validity
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "GGA"
    assert abs(parsed.latitude - 48.1173) < 0.001
    assert abs(parsed.longitude - 11.5167) < 0.001
    assert int(parsed.num_sats) == 8
    assert abs(float(parsed.horizontal_dil) - 0.9) < 0.001


def test_generate_gga_minimal(setup_args):
    """Test GGA generation with only position data."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    location.latitude = 48.1173
    location.longitude = 11.5167
    location_payload = keelson.enclose(location.SerializeToString())

    skarv.put("location_fix", create_zenoh_payload(location_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_gga()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "GGA"


def test_generate_rmc_complete(setup_args):
    """Test RMC generation with complete data."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    location.latitude = 48.1173
    location.longitude = 11.5167
    location_payload = keelson.enclose(location.SerializeToString())

    speed = TimestampedFloat()
    speed.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    speed.value = 22.4
    speed_payload = keelson.enclose(speed.SerializeToString())

    course = TimestampedFloat()
    course.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    course.value = 84.4
    course_payload = keelson.enclose(course.SerializeToString())

    skarv.put("location_fix", create_zenoh_payload(location_payload))
    skarv.put("speed_over_ground_knots", create_zenoh_payload(speed_payload))
    skarv.put("course_over_ground_deg", create_zenoh_payload(course_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_rmc()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "RMC"
    assert abs(float(parsed.spd_over_grnd) - 22.4) < 0.1
    assert abs(float(parsed.true_course) - 84.4) < 0.1


def test_generate_gll(setup_args):
    """Test GLL generation."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    location.latitude = 48.1173
    location.longitude = 11.5167
    location_payload = keelson.enclose(location.SerializeToString())

    skarv.put("location_fix", create_zenoh_payload(location_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_gll()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "GLL"
    assert abs(parsed.latitude - 48.1173) < 0.001


def test_generate_vtg(setup_args):
    """Test VTG generation."""
    speed = TimestampedFloat()
    speed.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    speed.value = 5.5
    speed_payload = keelson.enclose(speed.SerializeToString())

    course = TimestampedFloat()
    course.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    course.value = 54.7
    course_payload = keelson.enclose(course.SerializeToString())

    skarv.put("speed_over_ground_knots", create_zenoh_payload(speed_payload))
    skarv.put("course_over_ground_deg", create_zenoh_payload(course_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_vtg()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "VTG"
    assert abs(float(parsed.true_track) - 54.7) < 0.1
    assert abs(float(parsed.spd_over_grnd_kts) - 5.5) < 0.1


def test_generate_hdt(setup_args):
    """Test HDT generation."""
    heading = TimestampedFloat()
    heading.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    heading.value = 274.5
    heading_payload = keelson.enclose(heading.SerializeToString())

    skarv.put("heading_true_north_deg", create_zenoh_payload(heading_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_hdt()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "HDT"
    assert abs(float(parsed.heading) - 274.5) < 0.1


def test_generate_rot_conversion(setup_args):
    """Test ROT generation converts degrees/second to degrees/minute."""
    yaw_rate = TimestampedFloat()
    yaw_rate.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    yaw_rate.value = 0.05833  # degrees per second (3.5 deg/min)
    yaw_rate_payload = keelson.enclose(yaw_rate.SerializeToString())

    skarv.put("yaw_rate_degps", create_zenoh_payload(yaw_rate_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_rot()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "ROT"
    # Should be converted to degrees per minute
    assert abs(float(parsed.rate_of_turn) - 3.5) < 0.1


def test_generate_gsa(setup_args):
    """Test GSA generation."""
    hdop = TimestampedFloat()
    hdop.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    hdop.value = 1.3
    hdop_payload = keelson.enclose(hdop.SerializeToString())

    vdop = TimestampedFloat()
    vdop.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    vdop.value = 2.1
    vdop_payload = keelson.enclose(vdop.SerializeToString())

    pdop = TimestampedFloat()
    pdop.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 35, 19, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    pdop.value = 2.5
    pdop_payload = keelson.enclose(pdop.SerializeToString())

    skarv.put("location_fix_hdop", create_zenoh_payload(hdop_payload))
    skarv.put("location_fix_vdop", create_zenoh_payload(vdop_payload))
    skarv.put("location_fix_pdop", create_zenoh_payload(pdop_payload))

    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_gsa()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "GSA"
    assert abs(float(parsed.hdop) - 1.3) < 0.1
    assert abs(float(parsed.vdop) - 2.1) < 0.1
    assert abs(float(parsed.pdop) - 2.5) < 0.1


def test_generate_zda(setup_args):
    """Test ZDA generation (time-based)."""
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        generate_zda()

    output = captured_output.getvalue().strip()
    parsed = pynmea2.parse(output)
    assert parsed.sentence_type == "ZDA"
    # Should have current date/time
    assert parsed.year is not None
    assert parsed.month is not None
    assert parsed.day is not None
