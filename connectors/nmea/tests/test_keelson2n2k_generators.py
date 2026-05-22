#!/usr/bin/env python3

"""Tests for keelson2n2k - Keelson to NMEA2000 message generation.

The generators inject NMEA2000Message objects into a CAN gateway; these tests
drive them with skarv data and assert on the message handed to a mock runner.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

import skarv
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedInt
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality
from nmea2000.encoder import NMEA2000Encoder
from nmea2000.input_formats import N2KFormat

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))  # Make sibling imports work

# Import the script dynamically
script_path = bin_root / "keelson2n2k.py"
loader = SourceFileLoader("keelson2n2k", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
keelson2n2k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keelson2n2k)


# ==================== Helpers ====================


def _ts_now():
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)


def create_zenoh_payload(payload_bytes: bytes):
    """Create a zenoh Payload stand-in with a to_bytes() method."""
    zenoh_payload = Mock()
    zenoh_payload.to_bytes = Mock(return_value=payload_bytes)
    return zenoh_payload


@pytest.fixture
def setup_args():
    """Configure ARGS and a mock gateway RUNNER so generators emit messages."""
    keelson2n2k.ARGS = Mock()
    keelson2n2k.ARGS.source_address = 1
    keelson2n2k.ARGS.priority = 2
    keelson2n2k.RUNNER = Mock()
    yield
    keelson2n2k.ARGS = None
    keelson2n2k.RUNNER = None


def emitted_message(generator):
    """Run a generator and return the NMEA2000Message it injected, or None."""
    keelson2n2k.RUNNER.send.reset_mock()
    generator()
    if keelson2n2k.RUNNER.send.called:
        return keelson2n2k.RUNNER.send.call_args[0][0]
    return None


def fields_by_id(msg):
    """Map a message's fields by their id for easy assertions."""
    return {field.id: field for field in msg.fields}


def _put_float(subject: str, value: float):
    """Publish a TimestampedFloat sample onto a skarv subject."""
    payload = TimestampedFloat()
    payload.timestamp.FromNanoseconds(_ts_now())
    payload.value = value
    skarv.put(
        subject, create_zenoh_payload(keelson.enclose(payload.SerializeToString()))
    )


def _put_int(subject: str, value: int):
    """Publish a TimestampedInt sample onto a skarv subject."""
    payload = TimestampedInt()
    payload.timestamp.FromNanoseconds(_ts_now())
    payload.value = value
    skarv.put(
        subject, create_zenoh_payload(keelson.enclose(payload.SerializeToString()))
    )


def _populate_all_subjects():
    """Publish one sample for every subject the PGN generators consume."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(_ts_now())
    location.latitude = 12.345678
    location.longitude = 98.765432
    skarv.put(
        "location_fix",
        create_zenoh_payload(keelson.enclose(location.SerializeToString())),
    )
    _put_float("course_over_ground_deg", 123.4)
    _put_float("speed_over_ground_knots", 6.66)
    _put_float("heading_true_north_deg", 222.2)
    _put_float("yaw_deg", 11.1)
    _put_float("pitch_deg", 2.22)
    _put_float("roll_deg", 3.33)
    _put_int("location_fix_satellites_used", 11)
    _put_float("location_fix_hdop", 0.9)
    _put_float("location_fix_undulation_m", 41.5)
    _put_float("apparent_wind_speed_mps", 7.77)
    _put_float("apparent_wind_angle_deg", 33.3)
    _put_float("rudder_angle_deg", 12.34)
    _put_float("water_temperature_celsius", 41.5)
    _put_float("air_pressure_pa", 99100.0)


# ==================== SUBJECTS list ====================


def test_subject_list_valid():
    """All subjects in SUBJECTS are valid Keelson subjects."""
    for subject in keelson2n2k.SUBJECTS:
        assert (
            subject in keelson._SUBJECTS
        ), f"Subject '{subject}' is not a valid Keelson subject"


def test_no_invalid_wind_subjects():
    """The old invalid wind subject names are not used."""
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
    """The old invalid environmental subject names are not used."""
    for invalid in ["water_temperature_c", "atmospheric_pressure_pa"]:
        assert (
            invalid not in keelson2n2k.SUBJECTS
        ), f"Invalid subject '{invalid}' found in SUBJECTS list"


def test_no_depth_subject():
    """depth_below_transducer_m is not a Keelson subject and must not appear."""
    assert "depth_below_transducer_m" not in keelson2n2k.SUBJECTS


def test_correct_wind_subjects():
    """The correct wind subject names are used."""
    assert "apparent_wind_speed_mps" in keelson2n2k.SUBJECTS
    assert "apparent_wind_angle_deg" in keelson2n2k.SUBJECTS
    assert "true_wind_speed_mps" in keelson2n2k.SUBJECTS
    assert "true_wind_angle_deg" in keelson2n2k.SUBJECTS


def test_correct_env_subjects():
    """The correct environmental subject names are used."""
    assert "water_temperature_celsius" in keelson2n2k.SUBJECTS
    assert "air_pressure_pa" in keelson2n2k.SUBJECTS


# ==================== build_nmea2000_message ====================


def test_build_nmea2000_message_none_without_args():
    """Without ARGS (e.g. a stray trigger) no message is built."""
    keelson2n2k.ARGS = None
    assert keelson2n2k.build_nmea2000_message(129025, "x", "x", []) is None


# ==================== PGN generators ====================


def test_generate_pgn_129025_position(setup_args):
    """PGN 129025 carries the LocationFix latitude/longitude."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(_ts_now())
    location.latitude = 59.123456
    location.longitude = 18.654321
    skarv.put(
        "location_fix",
        create_zenoh_payload(keelson.enclose(location.SerializeToString())),
    )

    msg = emitted_message(keelson2n2k.generate_pgn_129025)
    assert msg is not None
    assert msg.PGN == 129025
    assert msg.id == "positionRapidUpdate"
    fields = fields_by_id(msg)
    assert fields["latitude"].value == pytest.approx(59.123456)
    assert fields["longitude"].value == pytest.approx(18.654321)


def test_generate_pgn_130306_wind_data_no_conversion(setup_args):
    """PGN 130306 keeps wind speed in m/s (no conversion to knots)."""
    wind_speed = TimestampedFloat()
    wind_speed.timestamp.FromNanoseconds(_ts_now())
    wind_speed.value = 10.0  # m/s

    wind_angle = TimestampedFloat()
    wind_angle.timestamp.FromNanoseconds(_ts_now())
    wind_angle.value = 45.0  # degrees

    skarv.put(
        "apparent_wind_speed_mps",
        create_zenoh_payload(keelson.enclose(wind_speed.SerializeToString())),
    )
    skarv.put(
        "apparent_wind_angle_deg",
        create_zenoh_payload(keelson.enclose(wind_angle.SerializeToString())),
    )

    msg = emitted_message(keelson2n2k.generate_pgn_130306)
    assert msg is not None
    assert msg.PGN == 130306
    assert msg.id == "windData"
    fields = fields_by_id(msg)
    assert fields["windSpeed"].value == 10.0  # unchanged, NOT converted
    assert fields["windSpeed"].unit_of_measurement == "m/s"
    assert fields["reference"].value == "Apparent"


def test_generate_pgn_130311_environmental_correct_subjects(setup_args):
    """PGN 130311 reads the correct environmental subjects."""
    water_temp = TimestampedFloat()
    water_temp.timestamp.FromNanoseconds(_ts_now())
    water_temp.value = 15.5  # Celsius

    air_pressure = TimestampedFloat()
    air_pressure.timestamp.FromNanoseconds(_ts_now())
    air_pressure.value = 101325.0  # Pa

    skarv.put(
        "water_temperature_celsius",
        create_zenoh_payload(keelson.enclose(water_temp.SerializeToString())),
    )
    skarv.put(
        "air_pressure_pa",
        create_zenoh_payload(keelson.enclose(air_pressure.SerializeToString())),
    )

    msg = emitted_message(keelson2n2k.generate_pgn_130311)
    assert msg is not None
    assert msg.PGN == 130311
    fields = fields_by_id(msg)
    # Temperature is converted to Kelvin.
    assert fields["temperature"].value == pytest.approx(15.5 + 273.15)
    assert fields["temperature"].unit_of_measurement == "K"
    assert fields["atmosphericPressure"].value == 101325.0
    assert fields["atmosphericPressure"].unit_of_measurement == "Pa"


def test_roundtrip_location_fix(setup_args):
    """LocationFix protobuf -> NMEA2000Message preserves the coordinates."""
    original_lat = 59.123456789
    original_lon = 18.987654321

    location = LocationFix()
    location.timestamp.FromNanoseconds(_ts_now())
    location.latitude = original_lat
    location.longitude = original_lon
    skarv.put(
        "location_fix",
        create_zenoh_payload(keelson.enclose(location.SerializeToString())),
    )

    msg = emitted_message(keelson2n2k.generate_pgn_129025)
    assert msg is not None
    assert msg.PGN == 129025
    assert len(msg.fields) == 2
    fields = fields_by_id(msg)
    assert fields["latitude"].value == pytest.approx(original_lat)
    assert fields["longitude"].value == pytest.approx(original_lon)


def test_roundtrip_wind_data(setup_args):
    """Wind data protobuf -> NMEA2000Message keeps m/s and converts angle."""
    original_speed_mps = 12.5
    original_angle_deg = 135.0

    wind_speed = TimestampedFloat()
    wind_speed.timestamp.FromNanoseconds(_ts_now())
    wind_speed.value = original_speed_mps

    wind_angle = TimestampedFloat()
    wind_angle.timestamp.FromNanoseconds(_ts_now())
    wind_angle.value = original_angle_deg

    skarv.put(
        "true_wind_speed_mps",
        create_zenoh_payload(keelson.enclose(wind_speed.SerializeToString())),
    )
    skarv.put(
        "true_wind_angle_deg",
        create_zenoh_payload(keelson.enclose(wind_angle.SerializeToString())),
    )

    msg = emitted_message(keelson2n2k.generate_pgn_130306)
    assert msg is not None
    assert msg.PGN == 130306
    assert msg.id == "windData"
    fields = fields_by_id(msg)
    # Wind speed stays in m/s.
    assert fields["windSpeed"].value == pytest.approx(original_speed_mps)
    assert fields["windSpeed"].unit_of_measurement == "m/s"
    # Wind angle is converted to radians.
    expected_angle_rad = original_angle_deg * 3.14159265359 / 180.0
    assert fields["windAngle"].value == pytest.approx(expected_angle_rad, rel=1e-5)


# ============ PGN 129029 location_fix_quality consumer ============


def _put_location_for_129029():
    location = LocationFix()
    location.timestamp.FromNanoseconds(_ts_now())
    location.latitude = 59.0
    location.longitude = 18.0
    skarv.put(
        "location_fix",
        create_zenoh_payload(keelson.enclose(location.SerializeToString())),
    )


def _pgn_129029_fields():
    msg = emitted_message(keelson2n2k.generate_pgn_129029)
    assert msg is not None, "Expected generate_pgn_129029 to emit a message"
    return {field.id: field.value for field in msg.fields}


def test_pgn_129029_consumes_rtk_fixed_quality(setup_args):
    quality = LocationFixQuality()
    quality.fix_type = LocationFixQuality.FIX_3D
    quality.pos_type = LocationFixQuality.POS_TYPE_RTK_INT
    quality.rtk_status = LocationFixQuality.RTK_STATUS_FIXED
    quality.integrity = LocationFixQuality.INTEGRITY_SAFE
    _put_location_for_129029()
    skarv.put(
        "location_fix_quality",
        create_zenoh_payload(keelson.enclose(quality.SerializeToString())),
    )

    fields = _pgn_129029_fields()
    assert fields["method"] == 4
    assert fields["integrity"] == 1


def test_pgn_129029_consumes_differential_quality(setup_args):
    quality = LocationFixQuality()
    quality.fix_type = LocationFixQuality.FIX_3D
    quality.pos_type = LocationFixQuality.POS_TYPE_PSRDIFF
    quality.rtk_status = LocationFixQuality.RTK_STATUS_DIFFERENTIAL
    _put_location_for_129029()
    skarv.put(
        "location_fix_quality",
        create_zenoh_payload(keelson.enclose(quality.SerializeToString())),
    )

    fields = _pgn_129029_fields()
    assert fields["method"] == 2


def test_pgn_129029_without_quality_completes_method_integrity(setup_args):
    """Without a LocationFixQuality the generator sets no method/integrity; the
    completion step still includes them (encode_pgn_129029 requires them)."""
    _put_location_for_129029()
    msg = emitted_message(keelson2n2k.generate_pgn_129029)
    assert msg is not None
    fields = fields_by_id(msg)
    # Present (encoder-required) but supplied by _complete -- a generator-set
    # method/integrity carries a value and no raw_value; a filler is raw_value=0.
    assert fields["method"].raw_value == 0
    assert fields["integrity"].raw_value == 0


# ==================== Encoder completeness ====================


def test_all_generated_pgns_encode(setup_args):
    """Every generate_pgn_* produces a message the nmea2000 encoder accepts.

    Regression guard for the missing-mandatory-fields bug, where only PGN
    129025 encoded. Also guards PGN_REQUIRED_FIELDS against nmea2000 library
    drift: a changed encoder field set surfaces here as an encode failure.
    """
    _populate_all_subjects()
    generators = [
        keelson2n2k.generate_pgn_129025,
        keelson2n2k.generate_pgn_129026,
        keelson2n2k.generate_pgn_129029,
        keelson2n2k.generate_pgn_127250,
        keelson2n2k.generate_pgn_127257,
        keelson2n2k.generate_pgn_130306,
        keelson2n2k.generate_pgn_127245,
        keelson2n2k.generate_pgn_130311,
    ]
    failures = []
    for generator in generators:
        msg = emitted_message(generator)
        assert msg is not None, f"{generator.__name__} emitted no message"
        try:
            NMEA2000Encoder().encode(msg, output_format=N2KFormat.CAN_FRAME_ASCII)
        except Exception as exc:
            failures.append(f"PGN {msg.PGN} ({generator.__name__}): {exc}")
    assert not failures, "PGNs failed to encode:\n" + "\n".join(failures)


def test_pgn_129029_with_quality_encodes(setup_args):
    """PGN 129029 with a LocationFixQuality (method/integrity set) encodes."""
    quality = LocationFixQuality()
    quality.fix_type = LocationFixQuality.FIX_3D
    quality.pos_type = LocationFixQuality.POS_TYPE_RTK_INT
    quality.rtk_status = LocationFixQuality.RTK_STATUS_FIXED
    quality.integrity = LocationFixQuality.INTEGRITY_SAFE
    _put_location_for_129029()
    skarv.put(
        "location_fix_quality",
        create_zenoh_payload(keelson.enclose(quality.SerializeToString())),
    )
    msg = emitted_message(keelson2n2k.generate_pgn_129029)
    assert msg is not None
    NMEA2000Encoder().encode(msg, output_format=N2KFormat.CAN_FRAME_ASCII)


def test_pgn_129029_satellite_count_uses_numberOfSvs(setup_args):
    """The satellite count rides under the encoder's field id, numberOfSvs."""
    _put_location_for_129029()
    _put_int("location_fix_satellites_used", 11)
    msg = emitted_message(keelson2n2k.generate_pgn_129029)
    assert msg is not None
    fields = fields_by_id(msg)
    assert "numberOfSvs" in fields
    assert "numberOfSatellites" not in fields
    assert fields["numberOfSvs"].value == 11
