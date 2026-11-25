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


def _transform(parent, child):
    return {
        "parent_frame_id": parent,
        "child_frame_id": child,
        "translation_m": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation_deg": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
    }


_POSITION = {"latitude_deg": 58.26, "longitude_deg": 12.24}


def test_an_explicit_frame_id_wins(connector):
    config = {
        "position": {**_POSITION, "frame_id": "mast/base_link"},
        "frame_transforms": [_transform("other/root", "other/lidar")],
    }
    assert connector._position_frame_id(config, "mast") == "mast/base_link"


def test_frame_id_defaults_to_the_single_root_of_the_tree(connector):
    """The root is what the transforms hang off, so it is what the fix must name.

    Defaulting to the entity id instead is the failure #285 describes: a fix that
    names no frame in the tree, and sensors that render unrotated.
    """
    config = {
        "position": dict(_POSITION),
        "frame_transforms": [
            _transform("mast/base_link", "mast/lidar"),
            _transform("mast/lidar", "mast/lidar_optical"),
            _transform("mast/base_link", "mast/radar"),
        ],
    }
    assert connector._position_frame_id(config, "mast") == "mast/base_link"


def test_frame_id_falls_back_to_the_entity_id_when_the_root_is_ambiguous(connector):
    config = {
        "position": dict(_POSITION),
        "frame_transforms": [
            _transform("a/base_link", "a/lidar"),
            _transform("b/base_link", "b/lidar"),
        ],
    }
    assert connector._position_frame_id(config, "mast") == "mast"


def test_frame_id_falls_back_to_the_entity_id_without_transforms(connector):
    assert connector._position_frame_id({"position": dict(_POSITION)}, "mast") == "mast"


def test_a_frame_id_outside_the_tree_is_reported(connector):
    config = {
        "platform_type": "sensor_station",
        "position": {**_POSITION, "frame_id": "mast/typo_link"},
        "frame_transforms": [_transform("mast/base_link", "mast/lidar")],
    }
    (warning,) = connector._position_warnings(config, "mast")
    assert "mast/typo_link" in warning


def test_a_position_on_a_vessel_is_reported(connector):
    config = {"platform_type": "vessel", "position": dict(_POSITION)}
    (warning,) = connector._position_warnings(config, "boat")
    assert "vessel" in warning


def test_the_sensor_station_example_draws_no_warning(connector):
    config = json.loads(
        (
            Path(__file__).resolve().parents[1] / "example-config-sensor-station.json"
        ).read_text()
    )
    assert connector._position_warnings(config, "storakrabban") == []


def test_no_position_draws_no_warning(connector):
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "example-config.json").read_text()
    )
    assert connector._position_warnings(config, "boat") == []
