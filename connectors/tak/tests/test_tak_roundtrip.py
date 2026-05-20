#!/usr/bin/env python3

"""Roundtrip tests for the TAK / CoT connectors."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import skarv

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedString
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

from conftest import tak2keelson, keelson2tak, create_zenoh_payload


def _decode_float(envelope_bytes):
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedFloat()
    msg.ParseFromString(payload)
    return msg.value


def _decode_string(envelope_bytes):
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedString()
    msg.ParseFromString(payload)
    return msg.value


def _decode_location_fix(envelope_bytes):
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = LocationFix()
    msg.ParseFromString(payload)
    return msg


FULL_COT = (
    b'<event version="2.0" uid="ANDROID-deadbeef" type="a-f-G-U-C-I" '
    b'time="2026-04-17T10:15:00Z" start="2026-04-17T10:15:00Z" '
    b'stale="2099-01-01T00:00:00Z" how="m-g">'
    b'<point lat="57.706" lon="11.937" hae="12.5" ce="5.0" le="3.0"/>'
    b"<detail>"
    b'<contact callsign="BRAVO"/>'
    b'<track course="270.0" speed="5.0"/>'
    b'<status battery="73"/>'
    b"</detail>"
    b"</event>"
)


def test_inbound_parse_emits_expected_subjects():
    subjects = dict(
        tak2keelson.parse_cot_event(
            FULL_COT, now=datetime(2026, 4, 18, tzinfo=timezone.utc)
        )
    )

    expected_keys = {
        "location_fix",
        "location_fix_accuracy_horizontal_m",
        "location_fix_accuracy_vertical_m",
        "course_over_ground_deg",
        "speed_over_ground_knots",
        "name",
        "battery_state_of_charge_pct",
    }
    assert expected_keys.issubset(subjects.keys())

    loc = _decode_location_fix(subjects["location_fix"])
    assert loc.latitude == pytest.approx(57.706)
    assert loc.longitude == pytest.approx(11.937)
    assert loc.altitude == pytest.approx(12.5)

    assert _decode_float(
        subjects["location_fix_accuracy_horizontal_m"]
    ) == pytest.approx(5.0)
    assert _decode_float(subjects["location_fix_accuracy_vertical_m"]) == pytest.approx(
        3.0
    )
    assert _decode_float(subjects["course_over_ground_deg"]) == pytest.approx(270.0)
    # CoT ships speed in m/s, keelson ships in knots
    assert _decode_float(subjects["speed_over_ground_knots"]) == pytest.approx(
        5.0 * 1.94384, rel=1e-4
    )
    assert _decode_string(subjects["name"]) == "BRAVO"
    assert _decode_float(subjects["battery_state_of_charge_pct"]) == pytest.approx(73.0)


def test_outbound_skarv_trigger_emits_valid_cot(setup_keelson2tak_args):
    """Seed skarv with all input subjects, fire the trigger, capture CoT XML."""
    import io
    import xml.etree.ElementTree as ET

    # Arrange: seed skarv as if zenoh `mirror` delivered these samples.
    skarv.put(
        "location_fix",
        create_zenoh_payload(
            tak2keelson._enclose_location_fix(lat=57.7, lon=11.9, hae=15.0)
        ),
    )
    skarv.put(
        "course_over_ground_deg",
        create_zenoh_payload(keelson.helpers.enclose_from_float(270.0)),
    )
    skarv.put(
        "speed_over_ground_knots",
        create_zenoh_payload(keelson.helpers.enclose_from_float(10.0)),
    )
    skarv.put(
        "name",
        create_zenoh_payload(keelson.helpers.enclose_from_string("LANDKRABBAN")),
    )

    # Act: capture the CoT XML the connector would write to the TAK TX queue.
    captured = io.BytesIO()
    with patch.object(keelson2tak, "_send_cot", side_effect=captured.write):
        keelson2tak._emit_cot()

    data = captured.getvalue()
    assert data, "expected CoT bytes to be produced"

    root = ET.fromstring(data)
    assert root.get("uid") == "test-uid"
    point = root.find("point")
    assert float(point.get("lat")) == pytest.approx(57.7)
    assert float(point.get("lon")) == pytest.approx(11.9)
    assert float(point.get("hae")) == pytest.approx(15.0)

    assert root.find("detail/contact").get("callsign") == "LANDKRABBAN"
    track = root.find("detail/track")
    assert float(track.get("course")) == pytest.approx(270.0)
    assert float(track.get("speed")) == pytest.approx(10.0 / 1.94384, rel=1e-4)
