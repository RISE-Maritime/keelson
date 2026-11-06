"""Calibration provenance and platform identity in the platform config (keelson #191)."""

import copy
import importlib.util
import json
import pathlib
from importlib.machinery import SourceFileLoader

import pytest
from jsonschema import ValidationError, validate

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parent.parent
_loader = SourceFileLoader(
    "platform_geometry2keelson_cal", str(ROOT / "bin" / "platform-geometry2keelson.py")
)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
platform = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platform)

SCHEMA = json.loads((ROOT / "config-schema.json").read_text())

# Logline's documented wire document (Logline docs/calibration.md, "What goes on
# the wire"): the block this schema now accepts unchanged.
LOGLINE = {
    "name": "sealog",
    "platform_type": "vessel",
    "frame_transforms": [
        {
            "parent_frame_id": "sealog-frame-ccrp",
            "child_frame_id": "sealog-frame-ouster-os-lidar",
            "sensor_type": "lidar",
            "sensor_description": "Ouster OS lidar",
            "translation_m": {"x": 0.22, "y": 0, "z": -0.35},
            "rotation_deg": {"yaw": 90, "pitch": 0, "roll": 0},
        }
    ],
    "calibration": {
        "frame": "x-forward, y-starboard, z-down; rotations yaw-pitch-roll",
        "zero": {
            "latitude": 57.708912345,
            "longitude": 11.97456,
            "altitude_m": 12.5,
            "accuracy_m": 3.4,
            "vertical_accuracy_m": 6.1,
            "scatter_m": 0.42,
            "heading_deg": 35,
            "heading_source": "baseline",
            "capture": "gnss_average",
            "samples": 60,
            "captured_at_ms": 1700000000000,
        },
        "sensors": [
            {"child_frame_id": "sealog-frame-ouster-os-lidar", "capture": "manual"}
        ],
        "updated_at_ms": 1700000001000,
    },
}


def test_loglines_documented_block_validates():
    validate(LOGLINE, SCHEMA)


def test_the_example_config_validates():
    validate(json.loads((ROOT / "example-config.json").read_text()), SCHEMA)


@pytest.mark.parametrize(
    "path, value",
    [
        (("zero", "accurracy_m"), 1.0),  # a typo is refused, not ignored
        (("sensors", 0, "uncertainty"), 1.0),
        (("bogus",), 1),
        (("zero", "accuracy_m"), -1.0),  # an accuracy is never negative
    ],
)
def test_the_block_refuses_what_it_does_not_define(path, value):
    doc = copy.deepcopy(LOGLINE)
    target = doc["calibration"]
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        validate(doc, SCHEMA)


def test_a_sensor_row_names_its_frame():
    doc = copy.deepcopy(LOGLINE)
    del doc["calibration"]["sensors"][0]["child_frame_id"]
    with pytest.raises(ValidationError):
        validate(doc, SCHEMA)


def test_the_served_config_always_says_what_kind_of_platform():
    """A platform's document identifies itself among every connector's
    configuration by `platform_type`; one left out reads as the default."""
    doc = copy.deepcopy(LOGLINE)
    del doc["platform_type"]
    platform._config.clear()
    platform._config.update(doc)
    try:
        assert platform.get_config()["platform_type"] == "vessel"
        assert "platform_type" not in platform._config  # served, not rewritten
    finally:
        platform._config.clear()
    assert SCHEMA["properties"]["platform_type"]["default"] == "vessel"


def test_a_calibration_row_for_an_unknown_frame_is_warned_about():
    doc = copy.deepcopy(LOGLINE)
    assert platform.calibration_warnings(doc) == []
    doc["calibration"]["sensors"].append({"child_frame_id": "sealog-frame-gone"})
    [warning] = platform.calibration_warnings(doc)
    assert "sealog-frame-gone" in warning
