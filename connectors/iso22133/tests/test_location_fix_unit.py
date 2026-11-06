"""The MONR position as a location_fix, and the sensor frame it carries (#310)."""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "iso22133_2keelson.py"
_loader = SourceFileLoader("iso22133_2keelson", str(BIN))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

ORIGIN = dict(origin_lat=57.7731, origin_lon=12.7708, origin_alt=10.0)
MONR = SimpleNamespace(x_m=0.0, y_m=111.32, z_m=2.0)


def _args(**overrides):
    return SimpleNamespace(**{**ORIGIN, "frame_id": "", **overrides})


def test_fix_is_at_the_converted_position():
    fix = bridge.build_location_fix(MONR, _args(), 1)
    assert fix.latitude == pytest.approx(57.7741, abs=1e-4)
    assert fix.longitude == pytest.approx(12.7708, abs=1e-6)
    assert fix.altitude == pytest.approx(12.0)


def test_frame_id_is_empty_unless_configured():
    assert bridge.build_location_fix(MONR, _args(), 1).frame_id == ""


def test_frame_id_carries_the_configured_frame():
    fix = bridge.build_location_fix(MONR, _args(frame_id="testobj/gnss"), 1)
    assert fix.frame_id == "testobj/gnss"


def test_no_fix_without_an_origin():
    assert bridge.build_location_fix(MONR, _args(origin_lat=None), 1) is None
