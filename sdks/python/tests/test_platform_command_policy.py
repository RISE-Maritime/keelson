"""The platform's command policy in connectors/platform/config-schema.json.

The control matrix, the safe-state policy with its fault and timeout tables, the RESET profile, the
independence level, the succession policy and the navigator's per-function ceilings. They live on the
platform entry so they round-trip to the platform connector like the rest of it, and the motion gate
(protocols/control_gate.yaml) reads them. Every fault row names the watchdog that trips it.
"""

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "connectors/platform/config-schema.json").read_text())
EXAMPLE = json.loads((ROOT / "connectors/platform/example-config.json").read_text())

MODES = ["manual_remote", "track_manual_power", "full_route", "dp_loiter"]


def edited(path, value=None, delete=False):
    """The example with one value changed (or deleted) at `path`."""
    doc = copy.deepcopy(EXAMPLE)
    node = doc
    for key in path[:-1]:
        node = node[key]
    if delete:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return doc


@pytest.mark.unit
def test_the_example_platform_declares_a_complete_policy_and_validates():
    jsonschema.validate(EXAMPLE, SCHEMA)
    assert EXAMPLE["command_profile"] == "test"
    assert EXAMPLE["safety_independence"] == "L1"
    faults = EXAMPLE["safe_state_policy"]["faults"]
    assert set(faults) == {
        "roc", "navigator", "hid", "gate", "mavlink", "authority_manager", "engineer",
        "payload", "payload_gate", "safety_supervisor", "local_lockout",
    }
    assert all(set(row["actions"]) == set(MODES) for row in faults.values())


@pytest.mark.unit
def test_a_platform_without_a_policy_still_validates():
    doc = {k: v for k, v in EXAMPLE.items() if k not in {
        "command_profile", "safety_independence", "responsible_roc", "active_control_station",
        "control_matrix", "safe_state_policy", "succession_policy", "navigation_functions",
    }}
    jsonschema.validate(doc, SCHEMA)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("why", "doc"),
    [
        ("a fault row with no watchdog is a wish",
         edited(["safe_state_policy", "faults", "roc", "watchdog"], delete=True)),
        ("a watchdog the table does not have",
         edited(["safe_state_policy", "faults", "roc", "watchdog"], "a_feeling")),
        ("an action the gate does not have",
         edited(["safe_state_policy", "faults", "hid", "actions", "manual_remote"], "PANIC")),
        ("a mode left out of a fault row",
         edited(["safe_state_policy", "faults", "navigator", "actions", "dp_loiter"], delete=True)),
        ("a loss left out of the table",
         edited(["safe_state_policy", "faults", "local_lockout"], delete=True)),
        ("a transition state that is nobody",
         edited(["safe_state_policy", "transition_state", "full_route"], "NONE")),
        ("a timeout of zero",
         edited(["safe_state_policy", "timeouts", "control_link_s"], 0)),
        ("a negative timeout",
         edited(["safe_state_policy", "timeouts", "guidance_s"], -1)),
        ("a timeout left out",
         edited(["safe_state_policy", "timeouts", "resolved_control_age_s"], delete=True)),
        ("a method ceiling above 5",
         edited(["navigation_functions", "route_execution", "max_control_method"], 6)),
        ("a navigation function left out",
         edited(["navigation_functions", "route_modification"], delete=True)),
        ("a profile that does not exist",
         edited(["command_profile"], "relaxed")),
        ("an independence level that does not exist",
         edited(["safety_independence"], "L6")),
        ("a source class that does not exist",
         edited(["control_matrix", "modes", "manual_remote", "dofs", "steering", "eligible_sources"], ["JOYSTICK"])),
        ("a DOF with no fallback",
         edited(["control_matrix", "modes", "full_route", "dofs", "throttle", "fallback"], delete=True)),
        ("a mode left out of the matrix",
         edited(["control_matrix", "modes", "dp_loiter"], delete=True)),
        ("SAFETY_TRIP without saying what humans get",
         edited(["control_matrix", "safety_trip", "humans"], delete=True)),
        ("a succession method that does not exist",
         edited(["succession_policy", "method"], "WHOEVER_IS_AWAKE")),
        ("an unknown key in the policy",
         edited(["safe_state_policy", "vibes"], "calm")),
        # Tightened after crowsnest-dev#77's pre-review (F10).
        ("a DOF vehicle_control has no axis for",
         edited(["control_matrix", "modes", "manual_remote", "dofs", "steerring"],
                {"eligible_sources": ["STATION_HELM"], "active_source": "STATION_HELM", "fallback": "HOLD"})),
        ("a coupled group naming something that is not a DOF",
         edited(["control_matrix", "modes", "full_route", "coupled"], [["steering", "rudder"]])),
        ("CONTINUE_FOR without saying for how long",
         edited(["safe_state_policy", "on_safety_supervisor_loss_while_active", "full_route"], "CONTINUE_FOR")),
        ("an empty site",
         edited(["responsible_roc"], "")),
        ("an empty principal among the SAFETY_TRIP processes",
         edited(["control_matrix", "safety_trip", "processes"], [""])),
        ("an empty deputy",
         edited(["succession_policy", "deputies"], [""])),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_policy_that_is_not_complete_is_refused(why, doc):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


@pytest.mark.unit
def test_continue_for_with_its_duration_validates():
    doc = edited(["safe_state_policy", "on_safety_supervisor_loss_while_active", "full_route"], "CONTINUE_FOR")
    doc["safe_state_policy"]["supervisor_loss_continue_for_s"] = 120
    jsonschema.validate(doc, SCHEMA)


@pytest.mark.unit
def test_a_thruster_dof_validates():
    doc = edited(["control_matrix", "modes", "dp_loiter", "dofs", "thruster/bow"],
                 {"eligible_sources": ["NAVIGATOR"], "active_source": "NAVIGATOR", "fallback": "NEUTRAL"})
    jsonschema.validate(doc, SCHEMA)


def _connector():
    """The connector module, loaded in-process, so set_config can be called."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "platform_geometry2keelson", ROOT / "connectors/platform/bin/platform-geometry2keelson.py"
    )
    module = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["platform-geometry2keelson"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


@pytest.mark.unit
def test_set_config_refuses_a_policy_change_and_takes_anything_else():
    """The policy changes only through maintenance -- edit the file and restart -- because set_config
    answers any caller (crowsnest-dev#77 pre-review F5)."""
    m = _connector()
    m._config.clear()
    m._config.update(copy.deepcopy(EXAMPLE))
    looser = copy.deepcopy(EXAMPLE)
    looser["safe_state_policy"]["faults"]["roc"]["actions"]["full_route"] = "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="only through maintenance"):
        m.set_config(looser)
    assert m._config["safe_state_policy"] == EXAMPLE["safe_state_policy"], "nothing applied"
    dropped = {k: v for k, v in EXAMPLE.items() if k != "command_profile"}
    with pytest.raises(ValueError, match="command_profile"):
        m.set_config(dropped)
    renamed = dict(copy.deepcopy(EXAMPLE), name="Renamed")
    m.set_config(renamed)
    assert m._config["name"] == "Renamed"
