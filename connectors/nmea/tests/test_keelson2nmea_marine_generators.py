#!/usr/bin/env python3

"""Tests for the keelson2nmea0183 wind, depth, water and XDR generators."""

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import pathlib
import sys
from unittest.mock import MagicMock, Mock, patch

import pynmea2
import pytest
import skarv
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat

bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))

script_path = bin_root / "keelson2nmea0183.py"
loader = SourceFileLoader("keelson2nmea0183", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
keelson2nmea0183 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keelson2nmea0183)


@pytest.fixture(autouse=True)
def setup_args():
    keelson2nmea0183.ARGS = Mock()
    keelson2nmea0183.ARGS.talker_id = "II"
    yield
    keelson2nmea0183.ARGS = None


def put_float(subject, value):
    """Store a TimestampedFloat sample for subject in skarv."""
    payload = TimestampedFloat()
    payload.value = value
    sample = MagicMock()
    sample.to_bytes = MagicMock(
        return_value=keelson.enclose(payload.SerializeToString())
    )
    skarv.put(subject, sample)


def capture(generator):
    """Run a generator and return the parsed NMEA sentences it wrote."""
    output = io.StringIO()
    with patch("sys.stdout", output):
        generator()
    return [pynmea2.parse(line) for line in output.getvalue().splitlines()]


def test_generate_mwv_apparent():
    put_float("apparent_wind_angle_deg", 330.0)
    put_float("apparent_wind_speed_mps", 3.4)

    (mwv,) = capture(keelson2nmea0183.generate_mwv_apparent)

    assert mwv.sentence_type == "MWV"
    assert mwv.reference == "R"
    assert float(mwv.wind_angle) == pytest.approx(330.0)
    assert float(mwv.wind_speed) == pytest.approx(3.4)
    assert mwv.wind_speed_units == "M"
    assert mwv.status == "A"


def test_generate_mwv_true():
    put_float("true_wind_angle_deg", 49.5)
    put_float("true_wind_speed_mps", 2.4)

    (mwv,) = capture(keelson2nmea0183.generate_mwv_true)

    assert mwv.reference == "T"
    assert float(mwv.wind_angle) == pytest.approx(49.5)


def test_generate_mwv_requires_angle_and_speed():
    put_float("apparent_wind_angle_deg", 10.0)
    assert capture(keelson2nmea0183.generate_mwv_apparent) == []


def test_generate_dpt():
    put_float("depth_below_transducer_m", 11.28)

    (dpt,) = capture(keelson2nmea0183.generate_dpt)

    assert dpt.sentence_type == "DPT"
    assert float(dpt.depth) == pytest.approx(11.28)


def test_generate_mtw():
    put_float("water_temperature_celsius", 23.7)

    (mtw,) = capture(keelson2nmea0183.generate_mtw)

    assert float(mtw.temperature) == pytest.approx(23.7)
    assert mtw.units == "C"


def test_generate_vhw_with_heading():
    put_float("speed_through_water_knots", 5.2)
    put_float("heading_true_north_deg", 57.1)

    (vhw,) = capture(keelson2nmea0183.generate_vhw)

    assert float(vhw.water_speed_knots) == pytest.approx(5.2)
    assert float(vhw.water_speed_km) == pytest.approx(9.6)
    assert float(vhw.heading_true) == pytest.approx(57.1)


def test_generate_vhw_without_heading():
    put_float("speed_through_water_knots", 5.2)

    (vhw,) = capture(keelson2nmea0183.generate_vhw)

    assert vhw.heading_true in (None, "")
    assert float(vhw.water_speed_knots) == pytest.approx(5.2)


def test_generate_xdr_attitude():
    put_float("yaw_deg", 1.25)
    put_float("pitch_deg", 0.75)
    put_float("roll_deg", -0.25)

    (xdr,) = capture(keelson2nmea0183.generate_xdr_attitude)

    transducers = [xdr.get_transducer(i) for i in range(xdr.num_transducers)]
    assert [(t.type, float(t.value), t.units, t.id) for t in transducers] == [
        ("A", 1.25, "D", "Yaw"),
        ("A", 0.75, "D", "Pitch"),
        ("A", -0.25, "D", "Roll"),
    ]


def test_generate_xdr_air_partial():
    put_float("air_pressure_pa", 102260.0)

    (xdr,) = capture(keelson2nmea0183.generate_xdr_air)

    transducer = xdr.get_transducer(0)
    assert xdr.num_transducers == 1
    assert (
        transducer.type,
        float(transducer.value),
        transducer.units,
        transducer.id,
    ) == (
        "P",
        102260.0,
        "P",
        "Baro",
    )


def test_generated_sentences_round_trip_through_nmea01832keelson():
    """What keelson2nmea0183 writes, nmea01832keelson reads back to the same values."""
    nmea_loader = SourceFileLoader(
        "nmea01832keelson_roundtrip", str(bin_root / "nmea01832keelson.py")
    )
    nmea_spec = importlib.util.spec_from_loader(nmea_loader.name, nmea_loader)
    nmea01832keelson = importlib.util.module_from_spec(nmea_spec)
    nmea_loader.exec_module(nmea01832keelson)

    put_float("apparent_wind_angle_deg", 330.0)
    put_float("apparent_wind_speed_mps", 3.4)
    put_float("depth_below_transducer_m", 11.28)
    put_float("water_temperature_celsius", 23.7)

    output = io.StringIO()
    with patch("sys.stdout", output):
        keelson2nmea0183.generate_mwv_apparent()
        keelson2nmea0183.generate_dpt()
        keelson2nmea0183.generate_mtw()

    published = []
    session = Mock()
    session.declare_publisher = Mock(
        side_effect=lambda key, **kwargs: Mock(
            put=Mock(side_effect=lambda data: published.append((key, data)))
        )
    )
    args = Mock(
        realm="rise",
        entity_id="case",
        source_id="rt",
        publish_raw=False,
        exclude_sentences=frozenset(),
    )

    for line in output.getvalue().splitlines():
        assert nmea01832keelson.process_line(line, session, args)

    values = {}
    for key, envelope in published:
        _, _, payload_bytes = keelson.uncover(envelope)
        payload = TimestampedFloat()
        payload.ParseFromString(payload_bytes)
        values[key.split("/pubsub/", 1)[1].split("/", 1)[0]] = payload.value

    assert values == pytest.approx(
        {
            "apparent_wind_angle_deg": 330.0,
            "apparent_wind_speed_mps": 3.4,
            "depth_below_transducer_m": 11.28,
            "water_temperature_celsius": 23.7,
        }
    )
