#!/usr/bin/env python3

"""Tests for keelson2n2k - Keelson to NMEA2000 JSON generation."""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
import json
import io
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock, patch
import pytest

import skarv
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from nmea2000.message import NMEA2000Message, NMEA2000Field

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))  # Make sibling imports work

# Import the script dynamically
script_path = bin_root / "keelson2n2k.py"
loader = SourceFileLoader("keelson2n2k", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
keelson2n2k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keelson2n2k)


# ==================== Helper to create Zenoh payloads ====================


def create_zenoh_payload(payload_bytes: bytes):
    """Create a zenoh Payload object with to_bytes() method."""
    zenoh_payload = MagicMock()
    zenoh_payload.to_bytes = MagicMock(return_value=payload_bytes)
    return zenoh_payload


# ==================== Fixtures ====================


@pytest.fixture
def setup_args():
    """Setup ARGS global for tests."""
    keelson2n2k.ARGS = Mock()
    keelson2n2k.ARGS.source_address = 1
    keelson2n2k.ARGS.priority = 2
    yield
    keelson2n2k.ARGS = None


def test_subject_list_valid():
    """Test that all subjects in SUBJECTS list are valid Keelson subjects"""
    import keelson

    for subject in keelson2n2k.SUBJECTS:
        assert (
            subject in keelson._SUBJECTS
        ), f"Subject '{subject}' is not a valid Keelson subject"


def test_no_invalid_wind_subjects():
    """Test that we're not using the old invalid wind subject names"""
    invalid_subjects = [
        "wind_speed_apparent_knots",
        "wind_angle_apparent_deg",
        "wind_speed_true_knots",
        "wind_angle_true_deg",
    ]

    for invalid in invalid_subjects:
        assert (
            invalid not in keelson2n2k.SUBJECTS
        ), f"Invalid subject '{invalid}' found in SUBJECTS list"


def test_no_invalid_env_subjects():
    """Test that we're not using the old invalid environmental subject names"""
    invalid_subjects = ["water_temperature_c", "atmospheric_pressure_pa"]

    for invalid in invalid_subjects:
        assert (
            invalid not in keelson2n2k.SUBJECTS
        ), f"Invalid subject '{invalid}' found in SUBJECTS list"


def test_no_depth_subject():
    """Test that depth_below_transducer_m is not in subjects (doesn't exist in Keelson)"""
    assert (
        "depth_below_transducer_m" not in keelson2n2k.SUBJECTS
    ), "depth_below_transducer_m is not a valid Keelson subject and should not be in SUBJECTS list"


def test_correct_wind_subjects():
    """Test that we're using the correct wind subject names"""
    assert "apparent_wind_speed_mps" in keelson2n2k.SUBJECTS
    assert "apparent_wind_angle_deg" in keelson2n2k.SUBJECTS
    assert "true_wind_speed_mps" in keelson2n2k.SUBJECTS
    assert "true_wind_angle_deg" in keelson2n2k.SUBJECTS


def test_correct_env_subjects():
    """Test that we're using the correct environmental subject names"""
    assert "water_temperature_celsius" in keelson2n2k.SUBJECTS
    assert "air_pressure_pa" in keelson2n2k.SUBJECTS


def test_output_json():
    """Test JSON output formatting"""
    import io
    from contextlib import redirect_stdout

    test_str = '{"test": "data"}'

    f = io.StringIO()
    with redirect_stdout(f):
        keelson2n2k.output_json(test_str)

    output = f.getvalue()
    assert output == test_str + "\n"


def test_create_nmea2000_message():
    """Test NMEA2000 message creation"""
    # Mock ARGS
    keelson2n2k.ARGS = Mock()
    keelson2n2k.ARGS.source_address = 10
    keelson2n2k.ARGS.priority = 3

    fields = [NMEA2000Field(id="test", name="Test", value=123)]

    json_str = keelson2n2k.create_nmea2000_message(
        129025, "testPgn", "Test PGN", fields
    )

    # Parse the JSON to verify structure
    msg_dict = json.loads(json_str)
    assert msg_dict["PGN"] == 129025
    assert msg_dict["id"] == "testPgn"
    assert msg_dict["description"] == "Test PGN"
    assert msg_dict["source"] == 10
    assert msg_dict["priority"] == 3
    assert msg_dict["destination"] == 255  # Broadcast
    assert len(msg_dict["fields"]) == 1
    assert msg_dict["fields"][0]["id"] == "test"


def test_generate_pgn_129025_position(setup_args):
    """Test PGN 129025 generation with LocationFix data"""
    # Create a LocationFix protobuf message
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    location.latitude = 59.123456
    location.longitude = 18.654321

    # Wrap in Keelson envelope and put in skarv
    location_payload = keelson.enclose(location.SerializeToString())
    skarv.put("location_fix", create_zenoh_payload(location_payload))

    # Capture stdout
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        keelson2n2k.generate_pgn_129025()

    # Parse and verify JSON output
    output = captured_output.getvalue().strip()
    assert output  # Should have output

    msg_dict = json.loads(output)
    assert msg_dict["PGN"] == 129025
    assert msg_dict["id"] == "positionRapidUpdate"
    assert len(msg_dict["fields"]) == 2

    # Verify latitude and longitude
    lat_field = next(f for f in msg_dict["fields"] if f["id"] == "latitude")
    lon_field = next(f for f in msg_dict["fields"] if f["id"] == "longitude")
    assert lat_field["value"] == 59.123456
    assert lon_field["value"] == 18.654321


def test_generate_pgn_130306_wind_data_no_conversion(setup_args):
    """Test PGN 130306 generation - verify wind speed stays in m/s (no conversion)"""
    # Create TimestampedFloat messages for apparent wind
    wind_speed = TimestampedFloat()
    wind_speed.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    wind_speed.value = 10.0  # 10 m/s

    wind_angle = TimestampedFloat()
    wind_angle.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    wind_angle.value = 45.0  # 45 degrees

    # Wrap in Keelson envelopes and put in skarv
    speed_payload = keelson.enclose(wind_speed.SerializeToString())
    angle_payload = keelson.enclose(wind_angle.SerializeToString())

    skarv.put("apparent_wind_speed_mps", create_zenoh_payload(speed_payload))
    skarv.put("apparent_wind_angle_deg", create_zenoh_payload(angle_payload))

    # Capture stdout
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        keelson2n2k.generate_pgn_130306()

    # Parse and verify JSON
    output = captured_output.getvalue().strip()
    assert output

    msg_dict = json.loads(output)
    assert msg_dict["PGN"] == 130306
    assert msg_dict["id"] == "windData"

    # Verify wind speed is still in m/s (no conversion to knots)
    speed_field = next(f for f in msg_dict["fields"] if f["id"] == "windSpeed")
    assert speed_field["value"] == 10.0  # Should still be 10.0 m/s, NOT converted
    assert speed_field["unit_of_measurement"] == "m/s"

    # Verify reference is Apparent
    ref_field = next(f for f in msg_dict["fields"] if f["id"] == "reference")
    assert ref_field["value"] == "Apparent"


def test_generate_pgn_130311_environmental_correct_subjects(setup_args):
    """Test PGN 130311 generation - verify correct subject names are used"""
    # Create TimestampedFloat messages
    water_temp = TimestampedFloat()
    water_temp.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    water_temp.value = 15.5  # Celsius

    air_pressure = TimestampedFloat()
    air_pressure.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    air_pressure.value = 101325.0  # Pa

    # Wrap in Keelson envelopes and put in skarv with CORRECT subject names
    temp_payload = keelson.enclose(water_temp.SerializeToString())
    pressure_payload = keelson.enclose(air_pressure.SerializeToString())

    skarv.put(
        "water_temperature_celsius", create_zenoh_payload(temp_payload)
    )  # CORRECT name
    skarv.put("air_pressure_pa", create_zenoh_payload(pressure_payload))  # CORRECT name

    # Capture stdout
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        keelson2n2k.generate_pgn_130311()

    # Parse and verify JSON
    output = captured_output.getvalue().strip()
    assert output

    msg_dict = json.loads(output)
    assert msg_dict["PGN"] == 130311
    assert msg_dict["id"] == "environmentalParameters"

    # Verify temperature is converted to Kelvin
    temp_field = next(f for f in msg_dict["fields"] if f["id"] == "temperature")
    assert temp_field["value"] == pytest.approx(15.5 + 273.15)
    assert temp_field["unit_of_measurement"] == "K"

    # Verify pressure
    pressure_field = next(
        f for f in msg_dict["fields"] if f["id"] == "atmosphericPressure"
    )
    assert pressure_field["value"] == 101325.0
    assert pressure_field["unit_of_measurement"] == "Pa"


def test_roundtrip_location_fix(setup_args):
    """Round-trip test: Keelson protobuf → NMEA2000 JSON → verify data integrity"""
    # Original data
    original_lat = 59.123456789
    original_lon = 18.987654321

    # Create Keelson protobuf message
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    location.latitude = original_lat
    location.longitude = original_lon

    # Encode to Keelson envelope and put in skarv
    location_payload = keelson.enclose(location.SerializeToString())
    skarv.put("location_fix", create_zenoh_payload(location_payload))

    # Generate NMEA2000 JSON
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        keelson2n2k.generate_pgn_129025()

    json_output = captured_output.getvalue().strip()
    assert json_output

    # Parse NMEA2000 JSON
    msg_dict = json.loads(json_output)

    # Verify data integrity through the round trip
    lat_field = next(f for f in msg_dict["fields"] if f["id"] == "latitude")
    lon_field = next(f for f in msg_dict["fields"] if f["id"] == "longitude")

    assert lat_field["value"] == pytest.approx(original_lat)
    assert lon_field["value"] == pytest.approx(original_lon)

    # Verify we can parse this JSON back with nmea2000 library
    msg = NMEA2000Message.from_json(json_output)
    assert msg.PGN == 129025
    assert len(msg.fields) == 2


def test_roundtrip_wind_data(setup_args):
    """Round-trip test: Wind data Keelson protobuf → NMEA2000 JSON → verify no conversion"""
    # Original data - wind speed in m/s
    original_speed_mps = 12.5
    original_angle_deg = 135.0

    # Create Keelson protobuf messages
    wind_speed = TimestampedFloat()
    wind_speed.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    wind_speed.value = original_speed_mps

    wind_angle = TimestampedFloat()
    wind_angle.timestamp.FromNanoseconds(
        int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    )
    wind_angle.value = original_angle_deg

    # Encode to Keelson envelopes and put in skarv
    speed_payload = keelson.enclose(wind_speed.SerializeToString())
    angle_payload = keelson.enclose(wind_angle.SerializeToString())

    skarv.put("true_wind_speed_mps", create_zenoh_payload(speed_payload))
    skarv.put("true_wind_angle_deg", create_zenoh_payload(angle_payload))

    # Generate NMEA2000 JSON
    captured_output = io.StringIO()
    with patch("sys.stdout", captured_output):
        keelson2n2k.generate_pgn_130306()

    json_output = captured_output.getvalue().strip()
    assert json_output

    # Parse NMEA2000 JSON
    msg_dict = json.loads(json_output)

    # CRITICAL: Verify wind speed stays in m/s (no conversion to knots)
    speed_field = next(f for f in msg_dict["fields"] if f["id"] == "windSpeed")
    assert speed_field["value"] == pytest.approx(
        original_speed_mps
    )  # Should be same value
    assert speed_field["unit_of_measurement"] == "m/s"

    # Verify angle is converted to radians
    angle_field = next(f for f in msg_dict["fields"] if f["id"] == "windAngle")
    expected_angle_rad = original_angle_deg * 3.14159265359 / 180.0
    assert angle_field["value"] == pytest.approx(expected_angle_rad, rel=1e-5)

    # Verify we can parse this JSON back with nmea2000 library
    msg = NMEA2000Message.from_json(json_output)
    assert msg.PGN == 130306
    assert msg.id == "windData"
