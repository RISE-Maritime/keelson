"""stream_mounts: which mount a position stream is measured at (keelson #310)."""

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
    "platform_geometry2keelson", str(ROOT / "bin" / "platform-geometry2keelson.py")
)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
platform = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platform)

SCHEMA = json.loads((ROOT / "config-schema.json").read_text())
EXAMPLE = json.loads((ROOT / "example-config.json").read_text())
KEY = "rise/@v0/ssrs18/pubsub/location_fix/rutx"
MOUNT = "ssrs18-demo-frame-gnss-rutx"


def _with_mounts(streams, **extra):
    config = copy.deepcopy(EXAMPLE)
    config["stream_mounts"] = {"v": 1, "streams": streams, **extra}
    return config


def test_the_example_declares_a_mount_and_validates():
    validate(EXAMPLE, SCHEMA)
    assert EXAMPLE["stream_mounts"]["streams"][KEY] == MOUNT
    assert platform.stream_mount_warnings(EXAMPLE) == []


def test_stream_mounts_is_optional():
    config = copy.deepcopy(EXAMPLE)
    del config["stream_mounts"]
    validate(config, SCHEMA)
    assert platform.stream_mount_warnings(config) == []


@pytest.mark.parametrize(
    "mounts",
    [
        {"v": 2, "streams": {KEY: MOUNT}},  # a shape this schema does not know
        {"v": 1, "streams": {KEY: 3}},  # a mount is a frame id
        {"v": 1, "streams": {KEY: ""}},  # ... and not an empty one
        {"v": 1},  # streams is required
        {"v": 1, "streams": {}, "frames": {}},  # no unknown keys
    ],
)
def test_the_schema_refuses_a_malformed_document(mounts):
    config = copy.deepcopy(EXAMPLE)
    config["stream_mounts"] = mounts
    with pytest.raises(ValidationError):
        validate(config, SCHEMA)


def test_warns_about_a_mount_that_is_not_in_frame_transforms():
    warnings = platform.stream_mount_warnings(_with_mounts({KEY: "ssrs18-nowhere"}))
    assert len(warnings) == 1
    assert "ssrs18-nowhere" in warnings[0]
    assert "frame_transforms" in warnings[0]


@pytest.mark.parametrize(
    "key",
    [
        "rise/@v0/ssrs18/pubsub/location_fix/**",
        "rise/@v0/*/pubsub/location_fix/rutx",
        "rise/@v0/ssrs18/pubsub/location_fix/rutx/@target/x",
    ],
)
def test_warns_about_a_key_that_names_more_than_one_stream(key):
    warnings = platform.stream_mount_warnings(_with_mounts({key: MOUNT}))
    assert any("not a concrete key" in w for w in warnings), warnings


def test_warns_about_a_key_that_is_not_a_pubsub_key():
    warnings = platform.stream_mount_warnings(
        _with_mounts({"location_fix/rutx": MOUNT})
    )
    assert any("is not a pubsub key" in w for w in warnings), warnings


def test_set_config_logs_the_warnings(caplog):
    with caplog.at_level("WARNING", logger="platform-geometry"):
        platform.set_config(_with_mounts({KEY: "ssrs18-nowhere"}))
    assert any("ssrs18-nowhere" in r.getMessage() for r in caplog.records)
