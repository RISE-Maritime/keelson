#!/usr/bin/env python3

"""
Roundtrip tests for AIS connectors.

Full pipeline: real AIS NMEA sentence -> pyais decode -> ais2keelson handlers
-> keelson envelopes -> skarv vault -> keelson2ais trigger -> stdout
-> pyais decode -> compare to original.
"""

import io
from unittest.mock import patch

import keelson
import skarv
from pyais import decode
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
    TimestampedString,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

from conftest import ais2keelson, keelson2ais, create_zenoh_payload


# ---------- helpers ----------


def _put_envelope(subject, envelope_bytes):
    """Store a keelson envelope in skarv via a mock Zenoh payload."""
    skarv.put(subject, create_zenoh_payload(envelope_bytes))


def _decode_envelope_float(envelope_bytes):
    """Decode a keelson envelope containing a TimestampedFloat."""
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedFloat()
    msg.ParseFromString(payload)
    return msg.value


def _decode_envelope_int(envelope_bytes):
    """Decode a keelson envelope containing a TimestampedInt."""
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedInt()
    msg.ParseFromString(payload)
    return msg.value


def _decode_envelope_string(envelope_bytes):
    """Decode a keelson envelope containing a TimestampedString."""
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedString()
    msg.ParseFromString(payload)
    return msg.value


def _decode_envelope_location(envelope_bytes):
    """Decode a keelson envelope containing a LocationFix."""
    _, _, payload = keelson.uncover(envelope_bytes)
    msg = LocationFix()
    msg.ParseFromString(payload)
    return msg


# ---------- roundtrip: message 1 ----------


def test_roundtrip_msg1(setup_keelson2ais_args):
    """AIS Message 1 roundtrip: AIVDM -> keelson -> AIVDM, compare fields."""
    # 1. Parse a real AIS Message 1 sentence
    original = decode(b"!AIVDM,1,1,,B,15NG6V0P01G?cFhE`R2IU?wn28R>,0*05")

    assert original.msg_type in (1, 2, 3)

    # 2. Run through ais2keelson handler -> keelson envelopes
    envelopes = dict(ais2keelson._handle_AIS_message_123(original))

    # 3. Put envelopes into skarv. The skarv trigger fires immediately when
    #    "location_fix" is put, so put it last so all other data is available.
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        for subject, envelope in envelopes.items():
            if subject != "location_fix":
                _put_envelope(subject, envelope)
        # Put location_fix last — this fires the trigger
        _put_envelope("location_fix", envelopes["location_fix"])

    # 4. Parse the output with pyais
    output_lines = captured.getvalue().strip().splitlines()
    assert len(output_lines) >= 1

    reconstructed = decode(*[line.encode() for line in output_lines])

    # 5. Compare key fields
    assert reconstructed.mmsi == original.mmsi
    assert abs(reconstructed.lat - original.lat) < 0.001
    assert abs(reconstructed.lon - original.lon) < 0.001
    assert abs(reconstructed.speed - original.speed) < 0.1
    assert abs(reconstructed.course - original.course) < 0.1
    assert abs(reconstructed.heading - original.heading) < 1.0


# ---------- roundtrip: message 5 ----------


def test_roundtrip_msg5(setup_keelson2ais_args):
    """AIS Message 5 roundtrip: AIVDM -> keelson -> AIVDM, compare fields."""
    # 1. Parse a real AIS Message 5 (two-part sentence)
    original = decode(
        b"!AIVDM,2,1,3,B,55?MbV02>H97ac<H4eEK6wtDkN0TDl622220j1p;166R0B440000000000,0*26",
        b"!AIVDM,2,2,3,B,00000000000,2*24",
    )

    assert original.msg_type == 5

    # 2. Run through ais2keelson handler -> keelson envelopes
    envelopes = dict(ais2keelson._handle_AIS_message_5(original))

    # 3. Put each envelope into skarv
    # Also need mmsi_number for keelson2ais to create message 5
    mmsi_envelope = keelson.helpers.enclose_from_integer(original.mmsi)
    _put_envelope("mmsi_number", mmsi_envelope)
    for subject, envelope in envelopes.items():
        _put_envelope(subject, envelope)

    # 4. Call send_message_5 and capture stdout
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        keelson2ais.send_message_5()

    # 5. Parse the output with pyais
    output_lines = captured.getvalue().strip().splitlines()
    assert len(output_lines) >= 1

    reconstructed = decode(*[line.encode() for line in output_lines])

    # 6. Compare key fields
    assert reconstructed.mmsi == original.mmsi
    assert abs(reconstructed.draught - original.draught) < 0.1
    # keelson2ais splits length/breadth evenly, so individual bow/stern may differ,
    # but total dimensions should match
    orig_length = original.to_bow + original.to_stern
    recon_length = reconstructed.to_bow + reconstructed.to_stern
    assert abs(recon_length - orig_length) < 1.0

    orig_breadth = original.to_port + original.to_starboard
    recon_breadth = reconstructed.to_port + reconstructed.to_starboard
    assert abs(recon_breadth - orig_breadth) < 1.0

    assert reconstructed.shipname.strip() == original.shipname.strip()
    assert reconstructed.callsign.strip() == original.callsign.strip()
    assert reconstructed.imo == original.imo


# ---------- roundtrip: message 18 ----------


def test_roundtrip_msg18(setup_keelson2ais_args):
    """AIS Message 18 roundtrip: AIVDM -> keelson -> AIVDM, compare fields.

    Message 18 (Class B) has no yaw_rate. The keelson2ais location_fix trigger
    generates a message type 1. We supplement with a zero yaw_rate so that the
    encode_dict call receives turn=0 instead of turn=None (which pyais does not
    handle correctly for encoding).
    """
    # 1. Parse an AIS Message 18 sentence with known values
    original = decode(b"!AIVDM,1,1,,B,B>eq`d@0>0=dsL8@IHPL@GP00000,0*53")

    assert original.msg_type == 18

    # 2. Run through ais2keelson handler -> keelson envelopes
    envelopes = dict(ais2keelson._handle_AIS_message_18(original))

    # 3. Put envelopes into skarv. The trigger fires on location_fix put,
    #    so put it last. Supplement with a zero yaw_rate since msg18 doesn't
    #    provide one.
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        for subject, envelope in envelopes.items():
            if subject != "location_fix":
                _put_envelope(subject, envelope)
        # Add a zero yaw_rate so encode_dict gets turn=0 (not None)
        _put_envelope(
            "yaw_rate_degps",
            keelson.helpers.enclose_from_float(0.0),
        )
        _put_envelope("location_fix", envelopes["location_fix"])

    # 4. Parse the output with pyais
    output_lines = captured.getvalue().strip().splitlines()
    assert len(output_lines) >= 1

    reconstructed = decode(*[line.encode() for line in output_lines])

    # 5. Compare the subset of fields available from msg18
    assert reconstructed.mmsi == original.mmsi
    assert abs(reconstructed.lat - original.lat) < 0.001
    assert abs(reconstructed.lon - original.lon) < 0.001
    assert abs(reconstructed.speed - original.speed) < 0.1
    assert abs(reconstructed.course - original.course) < 0.1


# ---------- full decode: real message 1 ----------


def test_full_decode_real_msg1():
    """Verify all keelson envelopes from a real AIS Message 1 decode correctly."""
    original = decode(b"!AIVDM,1,1,,B,15NG6V0P01G?cFhE`R2IU?wn28R>,0*05")

    envelopes = dict(ais2keelson._handle_AIS_message_123(original))

    # Check location_fix
    loc = _decode_envelope_location(envelopes["location_fix"])
    assert abs(loc.latitude - original.lat) < 0.0001
    assert abs(loc.longitude - original.lon) < 0.0001

    # Check yaw_rate (AIS deg/min -> keelson deg/s)
    yaw_rate = _decode_envelope_float(envelopes["yaw_rate_degps"])
    assert abs(yaw_rate - original.turn / 60.0) < 0.001

    # Check heading
    heading = _decode_envelope_float(envelopes["heading_true_north_deg"])
    assert abs(heading - original.heading) < 0.1

    # Check course
    course = _decode_envelope_float(envelopes["course_over_ground_deg"])
    assert abs(course - original.course) < 0.1

    # Check speed
    speed = _decode_envelope_float(envelopes["speed_over_ground_knots"])
    assert abs(speed - original.speed) < 0.1

    # Check MMSI
    mmsi = _decode_envelope_int(envelopes["mmsi_number"])
    assert mmsi == original.mmsi


# ---------- full decode: real message 5 ----------


def test_full_decode_real_msg5():
    """Verify all keelson envelopes from a real AIS Message 5 decode correctly."""
    original = decode(
        b"!AIVDM,2,1,3,B,55?MbV02>H97ac<H4eEK6wtDkN0TDl622220j1p;166R0B440000000000,0*26",
        b"!AIVDM,2,2,3,B,00000000000,2*24",
    )

    envelopes = dict(ais2keelson._handle_AIS_message_5(original))

    # Check draught
    draught = _decode_envelope_float(envelopes["draught_mean_m"])
    assert abs(draught - original.draught) < 0.1

    # Check length = to_bow + to_stern
    length = _decode_envelope_float(envelopes["length_over_all_m"])
    assert abs(length - (original.to_bow + original.to_stern)) < 0.1

    # Check breadth = to_port + to_starboard
    breadth = _decode_envelope_float(envelopes["breadth_over_all_m"])
    assert abs(breadth - (original.to_port + original.to_starboard)) < 0.1

    # Check name
    name = _decode_envelope_string(envelopes["name"])
    assert name.strip() == original.shipname.strip()

    # Check call sign
    callsign = _decode_envelope_string(envelopes["call_sign"])
    assert callsign.strip() == original.callsign.strip()

    # Check IMO
    imo = _decode_envelope_int(envelopes["imo_number"])
    assert imo == original.imo
