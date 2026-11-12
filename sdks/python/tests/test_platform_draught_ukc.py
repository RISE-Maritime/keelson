"""The vessel's draught and static under-keel clearance on the platform entry.

`draught_max_m` is a vessel attribute: the platform connector publishes it on the `draught_max_m`
subject, as it does length and breadth. `operational_limits.min_ukc_static_m` is a limit she operates
under. Neither is Route.Leg's `draught_max_m` (a waterway's limit) or `ukc_static_m` (a leg's planned
value).
"""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
PLATFORM = ROOT / "connectors/platform"
SCHEMA = json.loads((PLATFORM / "config-schema.json").read_text())
EXAMPLE = json.loads((PLATFORM / "example-config.json").read_text())


def with_(**changes):
    doc = copy.deepcopy(EXAMPLE)
    for key, value in changes.items():
        if key == "operational_limits":
            doc["operational_limits"] = {**doc["operational_limits"], **value}
        else:
            doc[key] = value
    return doc


@pytest.mark.unit
def test_the_example_declares_its_draught_and_ukc_and_validates():
    jsonschema.validate(EXAMPLE, SCHEMA)
    assert EXAMPLE["draught_max_m"] == 0.15
    assert EXAMPLE["operational_limits"]["min_ukc_static_m"] == 0.3


@pytest.mark.unit
def test_neither_is_required():
    doc = copy.deepcopy(EXAMPLE)
    del doc["draught_max_m"]
    del doc["operational_limits"]["min_ukc_static_m"]
    jsonschema.validate(doc, SCHEMA)


@pytest.mark.unit
@pytest.mark.parametrize(
    "doc",
    [
        with_(draught_max_m=-0.1),
        with_(draught_max_m="deep"),
        with_(operational_limits={"min_ukc_static_m": -1}),
        # The limits block is still closed: a typo is refused, not silently ignored.
        with_(operational_limits={"min_ukc_m": 0.5}),
    ],
    ids=["negative draught", "draught not a number", "negative UKC", "unknown limit"],
)
def test_a_value_that_is_not_one_is_refused(doc):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def _connector():
    """The connector module, loaded in-process, so its subject declaration can be called."""
    spec = importlib.util.spec_from_file_location("platform_geometry2keelson", PLATFORM / "bin/platform-geometry2keelson.py")
    module = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["platform-geometry2keelson"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


@pytest.mark.unit
def test_the_connector_declares_the_draught_subject_only_when_configured():
    subjects = _connector()._pubsub_subjects_for_config
    assert "draught_max_m" in subjects(EXAMPLE)
    without = {k: v for k, v in EXAMPLE.items() if k != "draught_max_m"}
    assert "draught_max_m" not in subjects(without)
    # The UKC is a limit, not a reading: it is in the configuration, not on a subject of its own.
    assert not any("ukc" in s for s in subjects(EXAMPLE))
