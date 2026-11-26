"""The vessel's maximum draught and static under-keel clearance in the platform config."""

import copy
import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import jsonschema
import pytest

PLATFORM = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((PLATFORM / "config-schema.json").read_text())
EXAMPLE = json.loads((PLATFORM / "example-config.json").read_text())


def _connector():
    loader = SourceFileLoader(
        "platform_geometry2keelson", str(PLATFORM / "bin/platform-geometry2keelson.py")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.unit
def test_the_example_with_both_values_validates():
    assert "draught_max_m" in EXAMPLE
    assert "min_ukc_static_m" in EXAMPLE["operational_limits"]
    jsonschema.validate(EXAMPLE, SCHEMA)


@pytest.mark.unit
def test_neither_value_is_required():
    doc = copy.deepcopy(EXAMPLE)
    del doc["draught_max_m"]
    del doc["operational_limits"]["min_ukc_static_m"]
    jsonschema.validate(doc, SCHEMA)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path", [("draught_max_m",), ("operational_limits", "min_ukc_static_m")]
)
def test_a_negative_value_is_refused(path):
    doc = copy.deepcopy(EXAMPLE)
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = -0.1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


@pytest.mark.unit
def test_the_draught_subject_is_declared_only_when_configured():
    subjects = _connector()._pubsub_subjects_for_config
    assert "draught_max_m" in subjects(EXAMPLE)
    without = {k: v for k, v in EXAMPLE.items() if k != "draught_max_m"}
    assert "draught_max_m" not in subjects(without)
