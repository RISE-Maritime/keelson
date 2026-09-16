#!/usr/bin/env python3

"""Tests for nmea01832keelson against a Yacht Devices YDEN-02 sentence mix.

Covers the wind, depth, water, GSV and XDR handlers, the NMEA 2000
encapsulation ($MXPGN / $PCDIN) and a replay of a captured YDEN stream.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from datetime import datetime, timezone
import logging
import pathlib
import struct
import sys
from unittest.mock import Mock

import pynmea2
import pytest
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedInt

bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))  # nmea01832keelson imports n2k2keelson
script_path = bin_root / "nmea01832keelson.py"
loader = SourceFileLoader("nmea01832keelson", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
nmea01832keelson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nmea01832keelson)

SAMPLES_FILE = pathlib.Path(__file__).resolve().parent / "data" / "yden_sample.txt"


@pytest.fixture(autouse=True)
def clear_module_state():
    nmea01832keelson.PUBLISHERS.clear()
    nmea01832keelson.n2k_handlers.PUBLISHERS.clear()
    nmea01832keelson.UNHANDLED_PGNS.clear()
    yield
    nmea01832keelson.PUBLISHERS.clear()
    nmea01832keelson.n2k_handlers.PUBLISHERS.clear()
    nmea01832keelson.UNHANDLED_PGNS.clear()


@pytest.fixture
def bus():
    """Session and args whose publishers record (key_expr, envelope) per put."""
    published = []
    session = Mock()

    def declare(key, **kwargs):
        publisher = Mock()
        publisher.put = Mock(side_effect=lambda data: published.append((key, data)))
        return publisher

    session.declare_publisher = Mock(side_effect=declare)
    args = Mock(
        realm="rise",
        entity_id="case",
        source_id="yden/nmea0183",
        publish_raw=False,
        mxpgn_byte_order="forward",
    )
    return session, args, published


def decoded(published, payload_type=TimestampedFloat):
    """Map '<subject>/<source_id>' to the decoded value of each publication."""
    values = {}
    for key, envelope in published:
        _, _, payload_bytes = keelson.uncover(envelope)
        payload = payload_type()
        payload.ParseFromString(payload_bytes)
        values[key.split("/pubsub/", 1)[1]] = payload.value
    return values


def run(handler, sentence, bus):
    session, args, published = bus
    handler(pynmea2.parse(sentence, check=False), session, args)
    return decoded(published)


# ==================== Wind ====================


def test_mwv_relative_publishes_apparent_wind(bus):
    values = run(nmea01832keelson.handle_mwv, "$YDMWV,45.0,R,3.1,M,A", bus)
    assert values == pytest.approx(
        {
            "apparent_wind_angle_deg/yden/nmea0183/MWV": 45.0,
            "apparent_wind_speed_mps/yden/nmea0183/MWV": 3.1,
        }
    )


def test_mwv_true_converts_knots(bus):
    values = run(nmea01832keelson.handle_mwv, "$YDMWV,300.5,T,10.0,N,A", bus)
    assert values["true_wind_angle_deg/yden/nmea0183/MWV"] == pytest.approx(300.5)
    assert values["true_wind_speed_mps/yden/nmea0183/MWV"] == pytest.approx(5.14444)


def test_mwv_invalid_status_publishes_nothing(bus):
    assert run(nmea01832keelson.handle_mwv, "$YDMWV,45.0,R,3.1,M,V", bus) == {}


def test_vwr_left_side_becomes_clockwise_angle(bus):
    values = run(nmea01832keelson.handle_vwr, "$YDVWR,30.0,L,6.6,N,3.4,M,12.2,K", bus)
    assert values == pytest.approx(
        {
            "apparent_wind_angle_deg/yden/nmea0183/VWR": 330.0,
            "apparent_wind_speed_mps/yden/nmea0183/VWR": 3.4,
        }
    )


def test_vwt_right_side(bus):
    values = run(nmea01832keelson.handle_vwt, "$YDVWT,49.5,R,4.6,N,2.4,M,8.5,K", bus)
    assert values == pytest.approx(
        {
            "true_wind_angle_deg/yden/nmea0183/VWT": 49.5,
            "true_wind_speed_mps/yden/nmea0183/VWT": 2.4,
        }
    )


def test_mwd_true_magnetic_and_speed(bus):
    values = run(nmea01832keelson.handle_mwd, "$YDMWD,321.1,T,323.8,M,5.0,N,2.6,M", bus)
    assert values == pytest.approx(
        {
            "true_wind_direction_deg/yden/nmea0183/MWD": 321.1,
            "true_wind_direction_deg/yden/nmea0183/MWD/magnetic": 323.8,
            "true_wind_speed_mps/yden/nmea0183/MWD": 2.6,
        }
    )


# ==================== Depth and water ====================


def test_dpt_zero_offset_publishes_transducer_depth_only(bus):
    values = run(nmea01832keelson.handle_dpt, "$YDDPT,11.28,0.00,", bus)
    assert values == pytest.approx(
        {"depth_below_transducer_m/yden/nmea0183/DPT": 11.28}
    )


def test_dpt_positive_offset_publishes_surface_depth(bus):
    values = run(nmea01832keelson.handle_dpt, "$YDDPT,11.28,0.50,", bus)
    assert values["depth_below_surface_m/yden/nmea0183/DPT"] == pytest.approx(11.78)


def test_dpt_negative_offset_publishes_keel_depth(bus):
    values = run(nmea01832keelson.handle_dpt, "$YDDPT,11.28,-1.20,", bus)
    assert values["depth_below_keel_m/yden/nmea0183/DPT"] == pytest.approx(10.08)
    assert "depth_below_surface_m/yden/nmea0183/DPT" not in values


def test_dpt_empty_depth_publishes_nothing(bus):
    assert run(nmea01832keelson.handle_dpt, "$YDDPT,,0.00,", bus) == {}


def test_dbt(bus):
    values = run(nmea01832keelson.handle_dbt, "$YDDBT,37.0,f,11.28,M,6.16,F", bus)
    assert values == pytest.approx(
        {"depth_below_transducer_m/yden/nmea0183/DBT": 11.28}
    )


def test_dbs(bus):
    values = run(nmea01832keelson.handle_dbs, "$YDDBS,37.0,f,11.28,M,6.16,F", bus)
    assert values == pytest.approx({"depth_below_surface_m/yden/nmea0183/DBS": 11.28})


def test_mtw(bus):
    values = run(nmea01832keelson.handle_mtw, "$YDMTW,23.7,C", bus)
    assert values == pytest.approx(
        {"water_temperature_celsius/yden/nmea0183/MTW": 23.7}
    )


def test_vbw_valid_water_speed(bus):
    values = run(nmea01832keelson.handle_vbw, "$YDVBW,1.20,0.05,A,0.01,-0.03,A", bus)
    assert values == pytest.approx({"speed_through_water_knots/yden/nmea0183/VBW": 1.2})


def test_vbw_invalid_water_speed_publishes_nothing(bus):
    # The YDEN sends ground speed only, with the water speed flagged V
    sentence = "$YDVBW,,,V,0.05,-0.03,A,,V,-0.05,A"
    assert run(nmea01832keelson.handle_vbw, sentence, bus) == {}


# ==================== GSV and XDR ====================


def test_gsv_first_message_publishes_satellites_visible(bus):
    session, args, published = bus
    sentence = "$YDGSV,3,1,12,05,21,181,29,10,10,323,35,12,25,198,37,13,33,202,40"
    nmea01832keelson.handle_gsv(pynmea2.parse(sentence, check=False), session, args)
    assert decoded(published, TimestampedInt) == {
        "location_fix_satellites_visible/yden/nmea0183/GSV/yd": 12
    }


def test_gsv_talkers_publish_distinct_series(bus):
    session, args, published = bus
    for sentence in (
        "$GPGSV,2,1,08,05,21,181,29,10,10,323,35,12,25,198,37,13,33,202,40",
        "$GLGSV,1,1,03,65,21,181,29,66,10,323,35,72,25,198,37",
    ):
        nmea01832keelson.handle_gsv(pynmea2.parse(sentence, check=False), session, args)
    assert decoded(published, TimestampedInt) == {
        "location_fix_satellites_visible/yden/nmea0183/GSV/gp": 8,
        "location_fix_satellites_visible/yden/nmea0183/GSV/gl": 3,
    }


def test_gsv_later_messages_publish_nothing(bus):
    sentence = "$YDGSV,3,2,12,14,16,042,33,15,86,014,31,17,14,073,32,19,13,107,39"
    assert run(nmea01832keelson.handle_gsv, sentence, bus) == {}


def test_xdr_attitude(bus):
    sentence = "$YDXDR,A,1.25,D,Yaw,A,0.75,D,Pitch,A,-0.25,D,Roll"
    values = run(nmea01832keelson.handle_xdr, sentence, bus)
    assert values == pytest.approx(
        {
            "yaw_deg/yden/nmea0183/XDR": 1.25,
            "pitch_deg/yden/nmea0183/XDR": 0.75,
            "roll_deg/yden/nmea0183/XDR": -0.25,
        }
    )


def test_xdr_air_temperature_and_pressure(bus):
    sentence = "$YDXDR,C,28.5,C,Air,P,102260,P,Baro"
    values = run(nmea01832keelson.handle_xdr, sentence, bus)
    assert values == pytest.approx(
        {
            "air_temperature_celsius/yden/nmea0183/XDR": 28.5,
            "air_pressure_pa/yden/nmea0183/XDR": 102260.0,
        }
    )


def test_xdr_pressure_in_bar_and_unknown_transducer(bus):
    sentence = "$YDXDR,P,1.0126,B,Baro,U,12.6,V,Battery"
    values = run(nmea01832keelson.handle_xdr, sentence, bus)
    assert values == pytest.approx({"air_pressure_pa/yden/nmea0183/XDR": 101260.0})


# ==================== NMEA 2000 encapsulation ====================


YDEN_PCDIN_FUEL = "$PCDIN,01F211,00065D6A,72,034E46FFFFFFFFFF*50"
YDEN_MXPGN_FUEL = "$MXPGN,01F211,6872,034E46FFFFFFFFFF*6E"


def with_checksum(body):
    """Return `$<body>*hh` with the NMEA XOR checksum."""
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}"


def field_values(msg):
    return {field.id: field.value for field in msg.fields}


def test_decode_pcdin_fluid_level():
    msg = nmea01832keelson.decode_n2k_sentence(YDEN_PCDIN_FUEL)
    fields = field_values(msg)
    assert (msg.PGN, msg.source) == (127505, 0x72)
    assert (fields["instance"], fields["type"]) == (3, "Fuel")
    assert fields["level"] == pytest.approx(71.992)
    assert fields["capacity"] is None


def test_decode_mxpgn_forward_matches_pcdin():
    mxpgn = nmea01832keelson.decode_n2k_sentence(YDEN_MXPGN_FUEL, "forward")
    pcdin = nmea01832keelson.decode_n2k_sentence(YDEN_PCDIN_FUEL)
    assert mxpgn.source == 0x72
    assert field_values(mxpgn) == field_values(pcdin)


def test_decode_mxpgn_reversed():
    data = bytes.fromhex("034E46FFFFFFFFFF")[::-1].hex().upper()
    line = with_checksum(f"MXPGN,01F211,6872,{data}")
    msg = nmea01832keelson.decode_n2k_sentence(line, "reversed")
    assert field_values(msg)["level"] == pytest.approx(71.992)


def test_decode_uses_reception_time():
    before = datetime.now(timezone.utc)
    msg = nmea01832keelson.decode_n2k_sentence(YDEN_PCDIN_FUEL)
    assert msg.timestamp >= before


def test_decode_rejects_bad_checksum():
    with pytest.raises(ValueError):
        nmea01832keelson.decode_n2k_sentence("$MXPGN,01F211,6872,034E46FFFFFFFFFF*00")


def test_fuel_level_published_by_n2k_handlers(bus):
    session, args, published = bus
    assert nmea01832keelson.handle_n2k_sentence(YDEN_MXPGN_FUEL, session, args)
    assert decoded(published) == pytest.approx(
        {"tank_level_pct/yden/nmea0183/mxpgn/114/fuel/3": 71.992}
    )


def test_gray_water_level(bus):
    session, args, published = bus
    line = "$PCDIN,01F211,00065DC0,70,21FE03FFFFFFFFFF*25"
    nmea01832keelson.handle_n2k_sentence(line, session, args)
    assert decoded(published) == pytest.approx(
        {"tank_level_pct/yden/nmea0183/pcdin/112/gray_water/1": 0x03FE * 0.004}
    )


def test_tank_level_and_capacity(bus):
    session, args, published = bus
    data = bytes([0x05]) + struct.pack("<hI", 25000, 2000) + b"\xff"
    line = with_checksum(f"PCDIN,01F211,00000000,07,{data.hex().upper()}")
    nmea01832keelson.handle_n2k_sentence(line, session, args)
    assert decoded(published) == pytest.approx(
        {
            "tank_level_pct/yden/nmea0183/pcdin/7/fuel/5": 100.0,
            "tank_capacity_l/yden/nmea0183/pcdin/7/fuel/5": 200.0,
        }
    )


def test_engine_speed_published_by_n2k_handlers(bus):
    session, args, published = bus
    # PGN 127488: instance 0, speed 6000 × 0.25 rpm, boost and trim not available
    data = bytes([0x00]) + struct.pack("<H", 6000) + b"\xff\xff\x7f\xff\xff"
    line = with_checksum(f"PCDIN,01F200,00000000,10,{data.hex().upper()}")
    assert nmea01832keelson.handle_n2k_sentence(line, session, args)
    assert decoded(published) == pytest.approx(
        {"engine_rate_rpm/yden/nmea0183/pcdin/16/0": 1500.0}
    )


def test_unhandled_pgn_returns_false(bus):
    session, args, published = bus
    # PGN 126992 System Time is known to the decoder but has no Keelson handler
    line = with_checksum("PCDIN,01EF10,00000000,10,FFF0FFFFFFFFFFFF")
    assert not nmea01832keelson.handle_n2k_sentence(line, session, args)
    assert published == []


# ==================== process_line ====================


def test_process_line_proprietary_sentence_does_not_raise(bus, caplog):
    session, args, published = bus
    with caplog.at_level(logging.DEBUG, logger="nmea01832keelson"):
        handled = nmea01832keelson.process_line(
            "$PGRME,15.0,M,45.0,M,25.0,M*1C", session, args
        )
    assert not handled
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_process_line_publishes_raw_before_parsing(bus):
    session, args, published = bus
    args.publish_raw = True
    nmea01832keelson.process_line("$MXPGN,garbage", session, args)
    assert [key.split("/pubsub/", 1)[1] for key, _ in published] == [
        "raw_nmea0183/yden/nmea0183"
    ]


def test_replay_captured_yden_stream(bus, caplog):
    session, args, published = bus
    lines = SAMPLES_FILE.read_text().splitlines()

    with caplog.at_level(logging.DEBUG, logger="nmea01832keelson"):
        handled = [
            nmea01832keelson.process_line(line, session, args, number)
            for number, line in enumerate(lines, start=1)
        ]

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    unhandled = {line.split(",", 1)[0] for line, ok in zip(lines, handled) if not ok}
    assert unhandled == {"$YDDTM"}

    subjects = {key.split("/pubsub/", 1)[1].split("/", 1)[0] for key, _ in published}
    assert subjects >= {
        "apparent_wind_angle_deg",
        "true_wind_speed_mps",
        "depth_below_transducer_m",
        "depth_below_surface_m",
        "water_temperature_celsius",
        "location_fix_satellites_visible",
        "pitch_deg",
        "roll_deg",
        "yaw_deg",
        "air_pressure_pa",
        "tank_level_pct",
    }
    assert subjects <= set(nmea01832keelson.NMEA0183_SUPPORTED_SUBJECTS)
