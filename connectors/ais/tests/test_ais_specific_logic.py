#!/usr/bin/env python3

"""
Unit tests for custom AIS-specific logic in ais2keelson and keelson2ais.

Tests conversion factors, dimension calculations, and position translation.
"""

import io
from unittest.mock import patch

import keelson
import skarv
from pyais import decode

from conftest import ais2keelson, keelson2ais, create_zenoh_payload


# ---------- helpers ----------


def _decode_float(envelope_bytes):
    from keelson.payloads.Primitives_pb2 import TimestampedFloat

    _, _, payload = keelson.uncover(envelope_bytes)
    msg = TimestampedFloat()
    msg.ParseFromString(payload)
    return msg.value


def _put_envelope(subject, envelope_bytes):
    skarv.put(subject, create_zenoh_payload(envelope_bytes))


# ==================== Position translation tests ====================


def test_translate_position_no_msg5():
    """_translate_position_to_geometrical_center returns early when MSG5_DB has no entry."""
    original = decode(b"!AIVDM,1,1,,B,15NG6V0P01G?cFhE`R2IU?wn28R>,0*05")

    orig_lat = original.lat
    orig_lon = original.lon

    # MSG5_DB is empty (cleared by autouse fixture)
    ais2keelson._translate_position_to_geometrical_center(original)

    # Position should be unchanged
    assert original.lat == orig_lat
    assert original.lon == orig_lon


def test_translate_position_corrects_to_center():
    """Verify position is offset based on vessel dimensions when MSG5_DB has an entry."""
    # Parse a position message
    msg123 = decode(b"!AIVDM,1,1,,B,15NG6V0P01G?cFhE`R2IU?wn28R>,0*05")
    orig_lat = msg123.lat
    orig_lon = msg123.lon

    # Parse a msg5 for the same MMSI and store it
    msg5 = decode(
        b"!AIVDM,2,1,3,B,55?MbV02>H97ac<H4eEK6wtDkN0TDl622220j1p;166R0B440000000000,0*26",
        b"!AIVDM,2,2,3,B,00000000000,2*24",
    )

    # Put msg5 in the DB (keyed by mmsi of msg123)
    ais2keelson.MSG5_DB[msg123.mmsi] = msg5

    # Apply the translation
    ais2keelson._translate_position_to_geometrical_center(msg123)

    # If the vessel has asymmetric dimensions, position should change.
    # With symmetric dimensions (to_bow == to_stern, to_port == to_starboard),
    # the offset would be zero. Check that the function at least runs.
    # If dimensions are asymmetric, lat/lon should differ.
    if msg5.to_bow != msg5.to_stern or msg5.to_port != msg5.to_starboard:
        # Position should have changed
        assert msg123.lat != orig_lat or msg123.lon != orig_lon
    else:
        # Symmetric - position unchanged (within floating-point tolerance)
        assert abs(msg123.lat - orig_lat) < 0.0001
        assert abs(msg123.lon - orig_lon) < 0.0001


# ==================== Yaw rate conversion tests ====================


def test_yaw_rate_ais_to_keelson():
    """ais2keelson divides turn by 60 (deg/min -> deg/s)."""
    original = decode(b"!AIVDM,1,1,,B,15NG6V0P01G?cFhE`R2IU?wn28R>,0*05")

    envelopes = dict(ais2keelson._handle_AIS_message_123(original))
    yaw_rate_keelson = _decode_float(envelopes["yaw_rate_degps"])

    # AIS turn is in deg/min, keelson should be deg/s
    assert abs(yaw_rate_keelson - original.turn / 60.0) < 0.0001


def test_yaw_rate_keelson_to_ais(setup_keelson2ais_args):
    """keelson2ais multiplies yaw_rate by 60 (deg/s -> deg/min)."""
    # Use a sentence with a real turn value (not the special -128 "not available")
    original = decode(b"!AIVDM,1,1,,B,11mg=5O2As0nkehQ1UR1i1N1P000,0*41")

    # Run through ais2keelson to get keelson envelopes
    envelopes = dict(ais2keelson._handle_AIS_message_123(original))

    # Put envelopes into skarv. The trigger fires on location_fix put,
    # so put it last to ensure all other data is available.
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        for subject, envelope in envelopes.items():
            if subject != "location_fix":
                _put_envelope(subject, envelope)
        _put_envelope("location_fix", envelopes["location_fix"])

    output_lines = captured.getvalue().strip().splitlines()
    assert len(output_lines) >= 1

    reconstructed = decode(*[line.encode() for line in output_lines])

    # The reconstructed turn (deg/min) should match the original turn (deg/min)
    assert abs(reconstructed.turn - original.turn) < 0.5


# ==================== Message 5 dimension tests ====================


def test_msg5_length_from_bow_stern():
    """ais2keelson: length_over_all_m = to_bow + to_stern."""
    original = decode(
        b"!AIVDM,2,1,3,B,55?MbV02>H97ac<H4eEK6wtDkN0TDl622220j1p;166R0B440000000000,0*26",
        b"!AIVDM,2,2,3,B,00000000000,2*24",
    )

    envelopes = dict(ais2keelson._handle_AIS_message_5(original))
    length = _decode_float(envelopes["length_over_all_m"])

    assert abs(length - (original.to_bow + original.to_stern)) < 0.001


def test_msg5_breadth_from_port_starboard():
    """ais2keelson: breadth_over_all_m = to_port + to_starboard."""
    original = decode(
        b"!AIVDM,2,1,3,B,55?MbV02>H97ac<H4eEK6wtDkN0TDl622220j1p;166R0B440000000000,0*26",
        b"!AIVDM,2,2,3,B,00000000000,2*24",
    )

    envelopes = dict(ais2keelson._handle_AIS_message_5(original))
    breadth = _decode_float(envelopes["breadth_over_all_m"])

    assert abs(breadth - (original.to_port + original.to_starboard)) < 0.001


def test_msg5_length_split_for_output(setup_keelson2ais_args):
    """keelson2ais splits length_over_all_m evenly into to_bow/to_stern."""
    original = decode(
        b"!AIVDM,2,1,3,B,55?MbV02>H97ac<H4eEK6wtDkN0TDl622220j1p;166R0B440000000000,0*26",
        b"!AIVDM,2,2,3,B,00000000000,2*24",
    )

    # Build keelson envelopes
    envelopes = dict(ais2keelson._handle_AIS_message_5(original))
    mmsi_envelope = keelson.helpers.enclose_from_integer(original.mmsi)

    # Put into skarv
    _put_envelope("mmsi_number", mmsi_envelope)
    for subject, envelope in envelopes.items():
        _put_envelope(subject, envelope)

    # Call send_message_5 and capture stdout
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        keelson2ais.send_message_5()

    output_lines = captured.getvalue().strip().splitlines()
    assert len(output_lines) >= 1

    reconstructed = decode(*[line.encode() for line in output_lines])

    # keelson2ais splits length evenly: to_bow = to_stern = length / 2
    orig_length = original.to_bow + original.to_stern
    assert abs(reconstructed.to_bow - orig_length / 2) < 1.0
    assert abs(reconstructed.to_stern - orig_length / 2) < 1.0

    # And total should match
    recon_length = reconstructed.to_bow + reconstructed.to_stern
    assert abs(recon_length - orig_length) < 1.0
