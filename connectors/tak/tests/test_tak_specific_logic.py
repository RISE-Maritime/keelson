#!/usr/bin/env python3

"""Unit tests for pure functions in the TAK connector."""

import pytest

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

from conftest import tak2keelson, keelson2tak


def _decode_float(envelope_bytes):
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedFloat()
    msg.ParseFromString(payload)
    return msg.value


def _decode_location_fix(envelope_bytes):
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = LocationFix()
    msg.ParseFromString(payload)
    return msg


COT_WITH_ACCURACY = (
    b'<event version="2.0" uid="ANDROID-abc" type="a-f-G-U-C-I" '
    b'time="2026-04-17T10:15:00Z" start="2026-04-17T10:15:00Z" '
    b'stale="2099-01-01T00:00:00Z" how="m-g">'
    b'<point lat="57.706" lon="11.937" hae="10.0" ce="5.0" le="3.0"/>'
    b'<detail><contact callsign="BRAVO"/></detail>'
    b"</event>"
)

COT_ACCURACY_UNKNOWN = (
    b'<event version="2.0" uid="ANDROID-xyz" type="a-f-G-U-C-I" '
    b'time="2026-04-17T10:15:00Z" start="2026-04-17T10:15:00Z" '
    b'stale="2099-01-01T00:00:00Z" how="m-g">'
    b'<point lat="57.706" lon="11.937" hae="10.0" ce="9999999.0" le="9999999.0"/>'
    b'<detail><contact callsign="ALPHA"/></detail>'
    b"</event>"
)


def test_sanitize_uid_replaces_slashes():
    assert tak2keelson._sanitize_uid("cot-foo/bar") == "cot-foo_bar"


def test_sanitize_uid_replaces_dots_colons_spaces():
    assert (
        tak2keelson._sanitize_uid("ANDROID-abc.def:1234 xyz")
        == "ANDROID-abc_def_1234_xyz"
    )


def test_sanitize_uid_idempotent():
    s = tak2keelson._sanitize_uid
    for uid in ("clean_uid-123", "with/slash", "a.b:c d", ""):
        assert s(s(uid)) == s(uid)


def test_sanitize_uid_passes_clean_through():
    assert tak2keelson._sanitize_uid("rise-landkrabban-self") == "rise-landkrabban-self"


def test_mps_to_knots():
    # 1 m/s = 1.94384 knots (ITU conversion factor used in the spec)
    assert tak2keelson._mps_to_knots(0.0) == pytest.approx(0.0)
    assert tak2keelson._mps_to_knots(1.0) == pytest.approx(1.94384, rel=1e-4)
    assert tak2keelson._mps_to_knots(10.0) == pytest.approx(19.4384, rel=1e-4)


def test_knots_to_mps():
    assert keelson2tak._knots_to_mps(0.0) == pytest.approx(0.0)
    assert keelson2tak._knots_to_mps(1.94384) == pytest.approx(1.0, rel=1e-4)
    assert keelson2tak._knots_to_mps(19.4384) == pytest.approx(10.0, rel=1e-4)


def test_knots_mps_roundtrip():
    for knots in (0.1, 1.0, 5.5, 25.0, 100.0):
        mps = keelson2tak._knots_to_mps(knots)
        assert tak2keelson._mps_to_knots(mps) == pytest.approx(knots, rel=1e-6)


def test_parse_cot_accuracy_present():
    subjects = dict(tak2keelson.parse_cot_event(COT_WITH_ACCURACY))
    assert _decode_float(
        subjects["location_fix_accuracy_horizontal_m"]
    ) == pytest.approx(5.0)
    assert _decode_float(subjects["location_fix_accuracy_vertical_m"]) == pytest.approx(
        3.0
    )


def test_parse_cot_ce_9999999_skipped():
    subjects = dict(tak2keelson.parse_cot_event(COT_ACCURACY_UNKNOWN))
    assert "location_fix_accuracy_horizontal_m" not in subjects


def test_parse_cot_le_9999999_skipped():
    subjects = dict(tak2keelson.parse_cot_event(COT_ACCURACY_UNKNOWN))
    assert "location_fix_accuracy_vertical_m" not in subjects


def _parse_xml(xml_bytes):
    import xml.etree.ElementTree as ET

    return ET.fromstring(xml_bytes)


def test_build_cot_ce_le_default_to_sentinel():
    xml = keelson2tak.build_cot_event(
        uid="u",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.0,
        lon=11.0,
    )
    point = _parse_xml(xml).find("point")
    assert point.get("ce") == "9999999.0"
    assert point.get("le") == "9999999.0"


def test_build_cot_ce_le_passed_through_when_present():
    xml = keelson2tak.build_cot_event(
        uid="u",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.0,
        lon=11.0,
        ce=4.0,
        le=2.0,
    )
    point = _parse_xml(xml).find("point")
    assert float(point.get("ce")) == pytest.approx(4.0)
    assert float(point.get("le")) == pytest.approx(2.0)


def test_enclose_location_fix_with_hae():
    env = tak2keelson._enclose_location_fix(lat=57.7, lon=11.9, hae=15.5)
    msg = _decode_location_fix(env)
    assert msg.latitude == pytest.approx(57.7)
    assert msg.longitude == pytest.approx(11.9)
    assert msg.altitude == pytest.approx(15.5)


def test_enclose_location_fix_no_hae_leaves_altitude_zero():
    env = tak2keelson._enclose_location_fix(lat=57.7, lon=11.9, hae=None)
    msg = _decode_location_fix(env)
    assert msg.latitude == pytest.approx(57.7)
    assert msg.longitude == pytest.approx(11.9)
    # proto3 scalar default is 0.0 when unset
    assert msg.altitude == 0.0


COT_STALE_IN_PAST = (
    b'<event version="2.0" uid="late" type="a-f-G-U-C-I" '
    b'time="2020-01-01T00:00:00Z" start="2020-01-01T00:00:00Z" '
    b'stale="2020-01-01T00:01:00Z" how="m-g">'
    b'<point lat="57.7" lon="11.9" hae="10.0" ce="5.0" le="3.0"/>'
    b"</event>"
)


def test_parse_cot_stale_in_past_yields_nothing():
    from datetime import datetime, timezone

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    subjects = list(tak2keelson.parse_cot_event(COT_STALE_IN_PAST, now=now))
    assert subjects == []


def test_parse_cot_stale_in_future_still_yields():
    from datetime import datetime, timezone

    now = datetime(2019, 1, 1, tzinfo=timezone.utc)
    subjects = dict(tak2keelson.parse_cot_event(COT_STALE_IN_PAST, now=now))
    assert "location_fix_accuracy_horizontal_m" in subjects


def test_build_cot_has_required_event_attributes():
    from datetime import datetime, timezone

    now = datetime(2026, 4, 17, 10, 15, 0, tzinfo=timezone.utc)
    xml = keelson2tak.build_cot_event(
        uid="rise-landkrabban-self",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.7,
        lon=11.9,
        now=now,
    )
    root = _parse_xml(xml)
    assert root.tag == "event"
    assert root.get("version") == "2.0"
    assert root.get("uid") == "rise-landkrabban-self"
    assert root.get("type") == "a-f-S-X"
    assert root.get("how") == "m-g"
    # time == start, stale = time + stale_seconds
    assert root.get("time") == root.get("start")
    assert root.get("time", "").startswith("2026-04-17T10:15:00")
    assert root.get("stale", "").startswith("2026-04-17T10:16:00")


def test_build_cot_with_callsign_adds_contact():
    xml = keelson2tak.build_cot_event(
        uid="u",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.0,
        lon=11.0,
        callsign="LANDKRABBAN",
    )
    contact = _parse_xml(xml).find("detail/contact")
    assert contact is not None
    assert contact.get("callsign") == "LANDKRABBAN"


def test_build_cot_without_callsign_omits_contact():
    xml = keelson2tak.build_cot_event(
        uid="u",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.0,
        lon=11.0,
        callsign=None,
    )
    assert _parse_xml(xml).find("detail/contact") is None


def test_resolve_callsign_prefers_name_subject():
    # When a name subject value is provided, it wins over the fallback.
    assert keelson2tak._resolve_callsign("FROM_SUBJECT", "FALLBACK") == "FROM_SUBJECT"


def test_resolve_callsign_falls_back_when_name_missing():
    assert keelson2tak._resolve_callsign(None, "FALLBACK") == "FALLBACK"


def test_resolve_callsign_returns_none_when_both_missing():
    assert keelson2tak._resolve_callsign(None, None) is None


def test_build_cot_with_track_adds_track_element():
    xml = keelson2tak.build_cot_event(
        uid="u",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.0,
        lon=11.0,
        course_deg=270.0,
        speed_knots=10.0,
    )
    track = _parse_xml(xml).find("detail/track")
    assert track is not None
    assert float(track.get("course")) == pytest.approx(270.0)
    # speed is emitted in m/s per CoT
    assert float(track.get("speed")) == pytest.approx(10.0 / 1.94384, rel=1e-4)


def test_build_cot_without_track_omits_track_element():
    xml = keelson2tak.build_cot_event(
        uid="u",
        cot_type="a-f-S-X",
        how="m-g",
        stale_seconds=60.0,
        lat=57.0,
        lon=11.0,
    )
    assert _parse_xml(xml).find("detail/track") is None
