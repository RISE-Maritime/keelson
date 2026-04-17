#!/usr/bin/env python3

"""
Unit tests for TAK-connector-specific logic.

Tests UID sanitization, CoT XML parsing, sentinel handling, speed conversion,
and CoT XML building.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import keelson
import skarv
from keelson.helpers import enclose_from_float, enclose_from_string
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

from conftest import tak2keelson, keelson2tak, create_zenoh_payload

# ==================== Helpers ====================


def _make_location_fix_envelope(lat: float, lon: float, alt: float = 0.0) -> bytes:
    """Create a keelson envelope containing a LocationFix."""
    payload = LocationFix()
    payload.timestamp.FromNanoseconds(time.time_ns())
    payload.latitude = lat
    payload.longitude = lon
    payload.altitude = alt
    return keelson.enclose(payload.SerializeToString())


def _put_envelope(subject: str, envelope_bytes: bytes):
    """Store a keelson envelope in skarv via a mock Zenoh payload."""
    skarv.put(subject, create_zenoh_payload(envelope_bytes))


def _decode_float(envelope_bytes: bytes) -> float:
    from keelson.payloads.Primitives_pb2 import TimestampedFloat

    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedFloat()
    msg.ParseFromString(payload)
    return msg.value


def _decode_string(envelope_bytes: bytes) -> str:
    from keelson.payloads.Primitives_pb2 import TimestampedString

    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedString()
    msg.ParseFromString(payload)
    return msg.value


def _decode_location_fix(envelope_bytes: bytes) -> LocationFix:
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = LocationFix()
    msg.ParseFromString(payload)
    return msg


# ==================== UID sanitization ====================


def test_sanitize_uid_clean():
    """sanitize_uid leaves alphanumeric, underscore, hyphen unchanged."""
    assert tak2keelson.sanitize_uid("RISE-LANDKRABBAN-0") == "RISE-LANDKRABBAN-0"


def test_sanitize_uid_dots_replaced():
    """sanitize_uid replaces dots with underscores."""
    assert tak2keelson.sanitize_uid("vessel.1.2") == "vessel_1_2"


def test_sanitize_uid_colon_replaced():
    """sanitize_uid replaces colons (common in ATAK UIDs) with underscores."""
    assert tak2keelson.sanitize_uid("ANDROID-12345:1") == "ANDROID-12345_1"


def test_sanitize_uid_empty():
    """sanitize_uid handles empty string."""
    assert tak2keelson.sanitize_uid("") == ""


def test_sanitize_uid_spaces_replaced():
    """sanitize_uid replaces spaces with underscores."""
    assert tak2keelson.sanitize_uid("my vessel") == "my_vessel"


# ==================== CoT XML parsing (tak2keelson) ====================

COT_FULL = b"""<event version="2.0"
       uid="LANDKRABBAN-SELF"
       type="a-f-S-X"
       time="2026-04-17T10:15:00Z"
       start="2026-04-17T10:15:00Z"
       stale="2026-04-17T10:16:00Z"
       how="m-g">
  <point lat="57.706" lon="11.937" hae="10.0" ce="5.0" le="2.0"/>
  <detail>
    <contact callsign="LANDKRABBAN" endpoint="*:-1:stcp"/>
    <track speed="2.5" course="270.0"/>
    <status battery="80"/>
  </detail>
</event>"""

COT_NO_DETAIL = b"""<event version="2.0" uid="BARE" type="a-u-G"
       time="2026-04-17T10:15:00Z" start="2026-04-17T10:15:00Z"
       stale="2026-04-17T10:16:00Z" how="h-e">
  <point lat="10.0" lon="20.0" hae="0.0" ce="9999999.0" le="9999999.0"/>
</event>"""

COT_UNKNOWN_CE_LE = b"""<event version="2.0" uid="UNKNOWN" type="a-u-G"
       time="2026-04-17T10:15:00Z" start="2026-04-17T10:15:00Z"
       stale="2026-04-17T10:16:00Z" how="h-e">
  <point lat="10.0" lon="20.0" hae="0.0" ce="9999999.0" le="9999999.0"/>
  <detail/>
</event>"""


def test_parse_cot_location_fix():
    """parse_cot_event extracts latitude and longitude into location_fix."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "location_fix" in result

    loc = _decode_location_fix(result["location_fix"])
    assert abs(loc.latitude - 57.706) < 1e-6
    assert abs(loc.longitude - 11.937) < 1e-6
    assert abs(loc.altitude - 10.0) < 1e-6


def test_parse_cot_horizontal_accuracy():
    """parse_cot_event extracts ce into location_fix_accuracy_horizontal_m."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "location_fix_accuracy_horizontal_m" in result
    ce = _decode_float(result["location_fix_accuracy_horizontal_m"])
    assert abs(ce - 5.0) < 1e-6


def test_parse_cot_vertical_accuracy():
    """parse_cot_event extracts le into location_fix_accuracy_vertical_m."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "location_fix_accuracy_vertical_m" in result
    le = _decode_float(result["location_fix_accuracy_vertical_m"])
    assert abs(le - 2.0) < 1e-6


def test_parse_cot_unknown_ce_le_skipped():
    """parse_cot_event skips ce/le when they equal the 9999999.0 sentinel."""
    result = dict(tak2keelson.parse_cot_event(COT_UNKNOWN_CE_LE))
    assert "location_fix_accuracy_horizontal_m" not in result
    assert "location_fix_accuracy_vertical_m" not in result


def test_parse_cot_course():
    """parse_cot_event extracts track/@course into course_over_ground_deg."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "course_over_ground_deg" in result
    course = _decode_float(result["course_over_ground_deg"])
    assert abs(course - 270.0) < 1e-6


def test_parse_cot_speed_mps_to_knots():
    """parse_cot_event converts track/@speed from m/s to knots."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "speed_over_ground_knots" in result
    knots = _decode_float(result["speed_over_ground_knots"])
    # 2.5 m/s * 1.94384 = 4.8596 knots
    expected = 2.5 * 1.94384
    assert abs(knots - expected) < 0.001


def test_parse_cot_callsign_to_name():
    """parse_cot_event maps detail/contact/@callsign to 'name'."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "name" in result
    name = _decode_string(result["name"])
    assert name == "LANDKRABBAN"


def test_parse_cot_battery():
    """parse_cot_event maps detail/status/@battery to battery_state_of_charge_pct."""
    result = dict(tak2keelson.parse_cot_event(COT_FULL))
    assert "battery_state_of_charge_pct" in result
    battery = _decode_float(result["battery_state_of_charge_pct"])
    assert abs(battery - 80.0) < 1e-6


def test_parse_cot_no_detail():
    """parse_cot_event handles events with no <detail> element."""
    result = dict(tak2keelson.parse_cot_event(COT_NO_DETAIL))
    assert "location_fix" in result
    assert "course_over_ground_deg" not in result
    assert "name" not in result


def test_parse_cot_invalid_xml():
    """parse_cot_event silently handles malformed XML."""
    result = list(tak2keelson.parse_cot_event(b"not valid xml"))
    assert result == []


def test_parse_cot_wrong_root_tag():
    """parse_cot_event ignores XML with a root tag other than 'event'."""
    result = list(tak2keelson.parse_cot_event(b"<message><foo/></message>"))
    assert result == []


def test_parse_cot_missing_point():
    """parse_cot_event returns nothing when <point> is absent."""
    xml = b"<event uid='X' type='a-u-G'><detail/></event>"
    result = list(tak2keelson.parse_cot_event(xml))
    assert result == []


# ==================== CoT stream splitter ====================


def test_split_cot_stream_single():
    """_split_cot_stream extracts a single complete event."""
    data = COT_FULL
    events, remainder = tak2keelson._split_cot_stream(data)
    assert len(events) == 1
    assert remainder == b""


def test_split_cot_stream_two_events():
    """_split_cot_stream extracts two complete events."""
    data = COT_FULL + b"\n" + COT_NO_DETAIL
    events, remainder = tak2keelson._split_cot_stream(data)
    assert len(events) == 2


def test_split_cot_stream_partial():
    """_split_cot_stream keeps incomplete data in remainder."""
    partial = b"<event uid='X'><point lat='1.0' lon='2.0' hae='0'/>"
    events, remainder = tak2keelson._split_cot_stream(partial)
    assert events == []
    assert partial in remainder


def test_split_cot_stream_empty():
    """_split_cot_stream handles empty buffer."""
    events, remainder = tak2keelson._split_cot_stream(b"")
    assert events == []
    assert remainder == b""


# ==================== UID extraction ====================


def test_get_uid_from_xml():
    """get_uid_from_xml extracts the uid attribute."""
    assert tak2keelson.get_uid_from_xml(COT_FULL) == "LANDKRABBAN-SELF"


def test_get_uid_from_xml_invalid():
    """get_uid_from_xml returns None for invalid XML."""
    assert tak2keelson.get_uid_from_xml(b"not xml") is None


# ==================== Stale extraction ====================


def test_get_stale_from_xml():
    """get_stale_from_xml returns a positive POSIX timestamp."""
    stale = tak2keelson.get_stale_from_xml(COT_FULL)
    assert stale is not None
    assert stale > 0


def test_get_stale_from_xml_invalid():
    """get_stale_from_xml returns None for invalid XML."""
    assert tak2keelson.get_stale_from_xml(b"bad xml") is None


# ==================== CoT XML building (keelson2tak) ====================


def test_build_cot_xml_no_location(mock_args):
    """build_cot_xml returns None when location_fix is absent from skarv."""
    result = keelson2tak.build_cot_xml(mock_args)
    assert result is None


def test_build_cot_xml_with_location(mock_args):
    """build_cot_xml produces valid XML when location_fix is present."""
    loc_envelope = _make_location_fix_envelope(57.706, 11.937, 10.0)
    _put_envelope("location_fix", loc_envelope)

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    assert xml_bytes is not None

    root = ET.fromstring(xml_bytes)
    assert root.tag == "event"
    assert root.attrib["uid"] == "test-uid"
    assert root.attrib["type"] == "a-f-S-X"
    assert root.attrib["how"] == "m-g"

    point = root.find("point")
    assert point is not None
    assert abs(float(point.attrib["lat"]) - 57.706) < 1e-6
    assert abs(float(point.attrib["lon"]) - 11.937) < 1e-6
    assert abs(float(point.attrib["hae"]) - 10.0) < 1e-6


def test_build_cot_xml_default_ce_le(mock_args):
    """build_cot_xml uses 9999999.0 sentinel when accuracy data is absent."""
    loc_envelope = _make_location_fix_envelope(10.0, 20.0)
    _put_envelope("location_fix", loc_envelope)

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    root = ET.fromstring(xml_bytes)
    point = root.find("point")
    assert float(point.attrib["ce"]) == 9999999.0
    assert float(point.attrib["le"]) == 9999999.0


def test_build_cot_xml_accuracy_fields(mock_args):
    """build_cot_xml uses accuracy values when present in skarv."""
    _put_envelope("location_fix", _make_location_fix_envelope(10.0, 20.0))
    _put_envelope("location_fix_accuracy_horizontal_m", enclose_from_float(3.5))
    _put_envelope("location_fix_accuracy_vertical_m", enclose_from_float(1.2))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    root = ET.fromstring(xml_bytes)
    point = root.find("point")
    assert abs(float(point.attrib["ce"]) - 3.5) < 1e-6
    assert abs(float(point.attrib["le"]) - 1.2) < 1e-6


def test_build_cot_xml_speed_knots_to_mps(mock_args):
    """build_cot_xml converts speed_over_ground_knots to m/s for CoT track."""
    _put_envelope("location_fix", _make_location_fix_envelope(10.0, 20.0))
    _put_envelope("speed_over_ground_knots", enclose_from_float(4.8596))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    root = ET.fromstring(xml_bytes)
    track = root.find(".//track")
    assert track is not None
    speed_mps = float(track.attrib["speed"])
    # 4.8596 knots / 1.94384 ≈ 2.5 m/s
    assert abs(speed_mps - 2.5) < 0.01


def test_build_cot_xml_course(mock_args):
    """build_cot_xml includes track/@course when course_over_ground_deg is set."""
    _put_envelope("location_fix", _make_location_fix_envelope(10.0, 20.0))
    _put_envelope("course_over_ground_deg", enclose_from_float(270.0))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    root = ET.fromstring(xml_bytes)
    track = root.find(".//track")
    assert track is not None
    assert abs(float(track.attrib["course"]) - 270.0) < 1e-6


def test_build_cot_xml_callsign_from_name_subject(mock_args):
    """build_cot_xml uses keelson 'name' subject over CLI --cot-callsign."""
    _put_envelope("location_fix", _make_location_fix_envelope(10.0, 20.0))
    _put_envelope("name", enclose_from_string("KEELSONSHIP"))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    root = ET.fromstring(xml_bytes)
    contact = root.find(".//contact")
    assert contact is not None
    assert contact.attrib["callsign"] == "KEELSONSHIP"


def test_build_cot_xml_callsign_fallback(mock_args):
    """build_cot_xml falls back to --cot-callsign when 'name' is absent."""
    mock_args.cot_callsign = "FALLBACKSHIP"
    _put_envelope("location_fix", _make_location_fix_envelope(10.0, 20.0))

    xml_bytes = keelson2tak.build_cot_xml(mock_args)
    root = ET.fromstring(xml_bytes)
    contact = root.find(".//contact")
    assert contact is not None
    assert contact.attrib["callsign"] == "FALLBACKSHIP"


def test_build_cot_xml_stale_in_future(mock_args):
    """build_cot_xml stale timestamp is in the future relative to time."""
    _put_envelope("location_fix", _make_location_fix_envelope(10.0, 20.0))

    now = datetime.now(timezone.utc)
    xml_bytes = keelson2tak.build_cot_xml(mock_args, now_utc=now)
    root = ET.fromstring(xml_bytes)
    stale_str = root.attrib["stale"]
    stale_dt = datetime.strptime(stale_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert stale_dt > now
