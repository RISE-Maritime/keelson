#!/usr/bin/env python3

"""Tests for the n2k2keelson engine, tank, battery, speed, depth and environment PGN handlers."""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat, TimestampedInt
from nmea2000.message import NMEA2000Message, NMEA2000Field

bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))

script_path = bin_root / "n2k2keelson.py"
loader = SourceFileLoader("n2k2keelson", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
n2k2keelson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n2k2keelson)


@pytest.fixture
def bus():
    """Session whose publishers record (key_expr, envelope) per put."""
    n2k2keelson.PUBLISHERS.clear()
    published = []
    session = Mock()
    session.declare_publisher = Mock(
        side_effect=lambda key, **kwargs: Mock(
            put=Mock(side_effect=lambda data: published.append((key, data)))
        )
    )
    yield session, published
    n2k2keelson.PUBLISHERS.clear()


def message(pgn, **fields):
    """Build an NMEA2000Message; a field is `id=value` or `id=(value, unit)`."""
    msg = NMEA2000Message(PGN=pgn, id=str(pgn), timestamp=datetime.now(timezone.utc))
    msg.fields = [
        NMEA2000Field(
            id=field_id,
            name=field_id,
            value=spec[0] if isinstance(spec, tuple) else spec,
            unit_of_measurement=spec[1] if isinstance(spec, tuple) else None,
        )
        for field_id, spec in fields.items()
    ]
    return msg


def run(pgn, bus, **fields):
    """Dispatch a message and map '<subject>/<source_id>' to published values."""
    session, published = bus
    n2k2keelson.dispatch_message(
        message(pgn, **fields), session, "rise", "case", "n2k/yden02/180"
    )
    values = {}
    for key, envelope in published:
        _, _, payload_bytes = keelson.uncover(envelope)
        payload = TimestampedFloat()
        payload.ParseFromString(payload_bytes)
        values[key.split("/pubsub/", 1)[1]] = payload.value
    return values


def test_127488_engine_speed_with_instance(bus):
    values = run(127488, bus, instance=1, speed=(1500.0, "rpm"), tiltTrim=(0, "%"))
    assert values == pytest.approx({"engine_rate_rpm/n2k/yden02/180/1": 1500.0})


def test_127489_engine_dynamic_conversions(bus):
    values = run(
        127489,
        bus,
        instance=0,
        oilPressure=(300000, "Pa"),
        oilTemperature=(363.15, "K"),
        temperature=(353.15, "K"),
        coolantPressure=(100000, "Pa"),
        fuelRate=(12.5, "L/h"),
        alternatorPotential=(14.1, "V"),
    )
    assert values == pytest.approx(
        {
            "engine_oil_pressure_psi/n2k/yden02/180/0": 43.5114,
            "engine_oil_temperature_celsius/n2k/yden02/180/0": 90.0,
            "engine_coolant_temperature_celsius/n2k/yden02/180/0": 80.0,
            "engine_coolant_pressure_psi/n2k/yden02/180/0": 14.5038,
            "engine_fuel_rate_lph/n2k/yden02/180/0": 12.5,
        }
    )


def test_127505_tank_type_and_instance(bus):
    values = run(
        127505,
        bus,
        instance=2,
        type="Black water",
        level=(40.0, "%"),
        capacity=(80.0, "L"),
    )
    assert values == pytest.approx(
        {
            "tank_level_pct/n2k/yden02/180/black_water/2": 40.0,
            "tank_capacity_l/n2k/yden02/180/black_water/2": 80.0,
        }
    )


def test_127505_water_is_fresh_water(bus):
    values = run(127505, bus, instance=0, type="Water", level=(55.0, "%"))
    assert values == pytest.approx(
        {"tank_level_pct/n2k/yden02/180/fresh_water/0": 55.0}
    )


def test_127506_dc_detailed_status(bus):
    values = run(
        127506, bus, instance=0, stateOfCharge=(87, "%"), timeRemaining=(7200, "s")
    )
    assert values == pytest.approx(
        {
            "battery_state_of_charge_pct/n2k/yden02/180/0": 87.0,
            "battery_time_remaining_s/n2k/yden02/180/0": 7200.0,
        }
    )


def test_127508_battery_status(bus):
    values = run(
        127508,
        bus,
        instance=1,
        voltage=(12.8, "V"),
        current=(-4.2, "A"),
        temperature=(298.15, "K"),
    )
    assert values == pytest.approx(
        {
            "battery_voltage_v/n2k/yden02/180/1": 12.8,
            "battery_current_a/n2k/yden02/180/1": -4.2,
            "battery_temperature_celsius/n2k/yden02/180/1": 25.0,
        }
    )


def test_128259_speed_through_water_in_knots(bus):
    values = run(
        128259,
        bus,
        speedWaterReferenced=(2.5, "m/s"),
        speedGroundReferenced=(2.7, "m/s"),
    )
    assert values == pytest.approx({"speed_through_water_knots/n2k/yden02/180": 4.8596})


@pytest.mark.parametrize(
    "offset, extra",
    [
        (0.0, {}),
        (0.5, {"depth_below_surface_m/n2k/yden02/180": 12.5}),
        (-1.5, {"depth_below_keel_m/n2k/yden02/180": 10.5}),
    ],
)
def test_128267_depth_and_offset(bus, offset, extra):
    values = run(128267, bus, depth=(12.0, "m"), offset=(offset, "m"))
    assert values == pytest.approx(
        {"depth_below_transducer_m/n2k/yden02/180": 12.0, **extra}
    )


@pytest.mark.parametrize(
    "source, subject",
    [
        ("Sea Temperature", "water_temperature_celsius"),
        ("Outside Temperature", "air_temperature_celsius"),
        ("Dew Point Temperature", "dew_point_celsius"),
    ],
)
def test_130312_temperature_sources(bus, source, subject):
    values = run(
        130312, bus, instance=0, source=source, actualTemperature=(293.15, "K")
    )
    assert values == pytest.approx({f"{subject}/n2k/yden02/180/0": 20.0})


def test_130316_extended_temperature(bus):
    values = run(
        130316, bus, instance=0, source="Sea Temperature", temperature=(288.15, "K")
    )
    assert values == pytest.approx({"water_temperature_celsius/n2k/yden02/180/0": 15.0})


def test_130312_unmapped_source_publishes_nothing(bus):
    values = run(
        130312,
        bus,
        instance=0,
        source="Engine Room Temperature",
        actualTemperature=(313.15, "K"),
    )
    assert values == {}


def test_130313_only_outside_humidity(bus):
    assert run(130313, bus, instance=0, source="Inside", actualHumidity=(40, "%")) == {}
    values = run(130313, bus, instance=0, source="Outside", actualHumidity=(71, "%"))
    assert values == pytest.approx({"air_relative_humidity_pct/n2k/yden02/180/0": 71.0})


def test_130314_only_atmospheric_pressure(bus):
    assert run(130314, bus, instance=0, source="Hydraulic", pressure=(5e6, "Pa")) == {}
    values = run(130314, bus, instance=0, source="Atmospheric", pressure=(101325, "Pa"))
    assert values == pytest.approx({"air_pressure_pa/n2k/yden02/180/0": 101325.0})


def test_new_handlers_declare_their_subjects():
    subjects = set(n2k2keelson.N2K_SUPPORTED_SUBJECTS)
    assert {
        "engine_rate_rpm",
        "tank_level_pct",
        "battery_voltage_v",
        "speed_through_water_knots",
        "depth_below_transducer_m",
        "air_relative_humidity_pct",
    } <= subjects


def test_127251_rate_of_turn_to_degrees_per_second(bus):
    values = run(127251, bus, sid=0, rate=(0.0174532925, "rad/s"))
    assert values == pytest.approx({"yaw_rate_degps/n2k/yden02/180": 1.0})


def test_127258_magnetic_variation_west_is_negative(bus):
    values = run(127258, bus, sid=0, variation=(-0.0139626, "rad"))
    assert values == pytest.approx(
        {"magnetic_variation_deg/n2k/yden02/180": -0.8}, abs=1e-4
    )


def test_129539_gnss_dops_skip_unavailable(bus):
    values = run(129539, bus, sid=0, hdop=0.9, vdop=1.4, tdop=None)
    assert values == pytest.approx(
        {
            "location_fix_hdop/n2k/yden02/180": 0.9,
            "location_fix_vdop/n2k/yden02/180": 1.4,
        }
    )


def test_129540_satellites_in_view_count(bus):
    session, published = bus
    n2k2keelson.dispatch_message(
        message(129540, sid=0, satsInView=11),
        session,
        "rise",
        "case",
        "n2k/yden02/180",
    )
    assert len(published) == 1
    key, envelope = published[0]
    assert key == "rise/@v0/case/pubsub/location_fix_satellites_visible/n2k/yden02/180"
    _, _, payload_bytes = keelson.uncover(envelope)
    payload = TimestampedInt()
    payload.ParseFromString(payload_bytes)
    assert payload.value == 11
