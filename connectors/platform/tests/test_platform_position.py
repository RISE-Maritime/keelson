"""Unit tests for the surveyed-position half of platform-geometry2keelson."""

import importlib.util
import json
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin" / "platform-geometry2keelson.py"


@pytest.fixture(scope="module")
def connector():
    spec = importlib.util.spec_from_file_location("platform_geometry", BIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.unit


def test_covariance_is_empty_when_no_accuracy_is_stated(connector):
    covariance, covariance_type = connector._position_covariance(None, None)
    assert covariance == []
    assert covariance_type == 0  # LocationFix.UNKNOWN


def test_horizontal_accuracy_is_drms_so_per_axis_variance_is_half(connector):
    """drms^2 = sigma_E^2 + sigma_N^2, so an isotropic per-axis variance is drms^2 / 2.

    Asserted explicitly: "simplifying" this to drms^2 would overstate the
    per-axis variance by a factor of two.
    """
    covariance, covariance_type = connector._position_covariance(1.3, 10.0)
    assert covariance[0] == pytest.approx(0.845)
    assert covariance[4] == pytest.approx(0.845)
    assert covariance[8] == pytest.approx(100.0)
    assert covariance[1:4] == [0.0, 0.0, 0.0]
    assert covariance_type == 1  # LocationFix.APPROXIMATED


def test_unstated_axis_gets_a_wide_variance_not_zero(connector):
    """Zero would claim perfect knowledge of the axis that was not surveyed."""
    covariance, _ = connector._position_covariance(1.3, None)
    assert covariance[8] == pytest.approx(connector._UNKNOWN_VARIANCE_M2)

    covariance, _ = connector._position_covariance(None, 10.0)
    assert covariance[0] == pytest.approx(connector._UNKNOWN_VARIANCE_M2)
    assert covariance[8] == pytest.approx(100.0)


def test_position_adds_location_fix_to_the_published_subjects(connector):
    config = {"position": {"latitude_deg": 58.26, "longitude_deg": 12.24}}
    assert connector._pubsub_subjects_for_config(config) == {
        "configuration_json",
        "location_fix",
    }

    config["position"]["accuracy_horizontal_m"] = 1.3
    config["position"]["accuracy_vertical_m"] = 10.0
    assert connector._pubsub_subjects_for_config(config) == {
        "configuration_json",
        "location_fix",
        "location_fix_accuracy_horizontal_m",
        "location_fix_accuracy_vertical_m",
    }


def test_a_platform_without_a_position_publishes_no_location_fix(connector):
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "example-config.json").read_text()
    )
    assert "location_fix" not in connector._pubsub_subjects_for_config(config)


def test_the_sensor_station_example_publishes_a_location_fix(connector):
    config = json.loads(
        (
            Path(__file__).resolve().parents[1] / "example-config-sensor-station.json"
        ).read_text()
    )
    subjects = connector._pubsub_subjects_for_config(config)
    assert "location_fix" in subjects
    assert "location_fix_accuracy_horizontal_m" in subjects
