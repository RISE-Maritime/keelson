#!/usr/bin/env python3

"""
Roundtrip tests for the TAK connector.

Pipeline: keelson subjects -> keelson2tak.build_cot_xml() -> CoT XML
       -> tak2keelson.parse_cot_event() -> keelson subjects.

Verifies that numeric values survive the round-trip within acceptable tolerance.
"""

import time

import keelson
import skarv
from keelson.helpers import enclose_from_float, enclose_from_string
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedString

from conftest import tak2keelson, keelson2tak, create_zenoh_payload

# ==================== Helpers ====================


def _make_location_fix_envelope(lat: float, lon: float, alt: float = 0.0) -> bytes:
    payload = LocationFix()
    payload.timestamp.FromNanoseconds(time.time_ns())
    payload.latitude = lat
    payload.longitude = lon
    payload.altitude = alt
    return keelson.enclose(payload.SerializeToString())


def _put_envelope(subject: str, envelope_bytes: bytes):
    skarv.put(subject, create_zenoh_payload(envelope_bytes))


def _decode_location_fix(envelope_bytes: bytes) -> LocationFix:
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = LocationFix()
    msg.ParseFromString(payload)
    return msg


def _decode_float(envelope_bytes: bytes) -> float:
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedFloat()
    msg.ParseFromString(payload)
    return msg.value


def _decode_string(envelope_bytes: bytes) -> str:
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedString()
    msg.ParseFromString(payload)
    return msg.value


# ==================== Full roundtrip: location only ====================


def test_roundtrip_location_fix(mock_args):
    """Location lat/lon/alt survive keelson -> CoT -> keelson."""
    # 1. Put location_fix into skarv
    _put_envelope("location_fix", _make_location_fix_envelope(57.706, 11.937, 10.0))

    # 2. Build CoT XML
    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    assert xml_bytes is not None

    # 3. Parse CoT XML back
    result = dict(tak2keelson.parse_cot_event(xml_bytes))
    assert "location_fix" in result

    loc = _decode_location_fix(result["location_fix"])
    assert abs(loc.latitude - 57.706) < 1e-6
    assert abs(loc.longitude - 11.937) < 1e-6
    assert abs(loc.altitude - 10.0) < 1e-6


# ==================== Full roundtrip: speed ====================


def test_roundtrip_speed(mock_args):
    """Speed (knots) survives keelson -> CoT (m/s) -> keelson (knots)."""
    _put_envelope("location_fix", _make_location_fix_envelope(57.0, 11.0))
    _put_envelope("speed_over_ground_knots", enclose_from_float(5.0))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    result = dict(tak2keelson.parse_cot_event(xml_bytes))

    assert "speed_over_ground_knots" in result
    speed_knots = _decode_float(result["speed_over_ground_knots"])
    # 5.0 knots -> m/s -> knots: should be within 0.01 knots
    assert abs(speed_knots - 5.0) < 0.01


# ==================== Full roundtrip: course ====================


def test_roundtrip_course(mock_args):
    """Course (degrees) survives keelson -> CoT -> keelson without conversion."""
    _put_envelope("location_fix", _make_location_fix_envelope(57.0, 11.0))
    _put_envelope("course_over_ground_deg", enclose_from_float(135.5))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    result = dict(tak2keelson.parse_cot_event(xml_bytes))

    assert "course_over_ground_deg" in result
    course = _decode_float(result["course_over_ground_deg"])
    assert abs(course - 135.5) < 1e-4


# ==================== Full roundtrip: callsign ====================


def test_roundtrip_name(mock_args):
    """Callsign/name survives keelson -> CoT -> keelson."""
    _put_envelope("location_fix", _make_location_fix_envelope(57.0, 11.0))
    _put_envelope("name", enclose_from_string("MYSHIP"))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    result = dict(tak2keelson.parse_cot_event(xml_bytes))

    assert "name" in result
    assert _decode_string(result["name"]) == "MYSHIP"


# ==================== Full roundtrip: accuracy ====================


def test_roundtrip_accuracy(mock_args):
    """Horizontal and vertical accuracy survive keelson -> CoT -> keelson."""
    _put_envelope("location_fix", _make_location_fix_envelope(57.0, 11.0))
    _put_envelope("location_fix_accuracy_horizontal_m", enclose_from_float(4.5))
    _put_envelope("location_fix_accuracy_vertical_m", enclose_from_float(2.1))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    result = dict(tak2keelson.parse_cot_event(xml_bytes))

    assert "location_fix_accuracy_horizontal_m" in result
    assert "location_fix_accuracy_vertical_m" in result
    assert abs(_decode_float(result["location_fix_accuracy_horizontal_m"]) - 4.5) < 1e-6
    assert abs(_decode_float(result["location_fix_accuracy_vertical_m"]) - 2.1) < 1e-6


# ==================== Sentinel values are not published ====================


def test_roundtrip_unknown_ce_le_not_published(mock_args):
    """When accuracy data is absent, CoT uses sentinel 9999999.0 which is not re-published."""
    _put_envelope("location_fix", _make_location_fix_envelope(57.0, 11.0))
    # No accuracy data in skarv -> build_cot_xml emits sentinel 9999999.0
    # -> parse_cot_event should NOT re-publish those subjects

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    result = dict(tak2keelson.parse_cot_event(xml_bytes))

    assert "location_fix_accuracy_horizontal_m" not in result
    assert "location_fix_accuracy_vertical_m" not in result


# ==================== UID construction matches spec ====================


def test_target_id_prefix(mock_args):
    """CoT UID sanitized to cot_{sanitized_uid} format."""
    uid = "RISE-LANDKRABBAN.1"
    sanitized = tak2keelson.sanitize_uid(uid)
    target_id = f"cot_{sanitized}"
    assert target_id == "cot_RISE-LANDKRABBAN_1"


def test_target_id_from_xml_roundtrip(mock_args):
    """UID in CoT XML leads to correct cot_ prefixed target_id."""
    _put_envelope("location_fix", _make_location_fix_envelope(57.0, 11.0))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    uid = tak2keelson.get_uid_from_xml(xml_bytes)
    assert uid == "test-uid"
    target_id = f"cot_{tak2keelson.sanitize_uid(uid)}"
    assert target_id == "cot_test-uid"
