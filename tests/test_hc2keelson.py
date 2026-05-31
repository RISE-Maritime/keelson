"""Sanity tests that catch subject/protocol regressions."""

import struct

import keelson
import pytest


def test_normalize_axis_boundaries(hc2keelson):
    # int16 min/max map symmetrically with the 32768 divisor
    assert hc2keelson.normalize_axis(0) == 0.0
    assert hc2keelson.normalize_axis(-32768) == -100.0
    assert abs(hc2keelson.normalize_axis(32767) - 99.99694824) < 1e-6
    assert hc2keelson.normalize_axis(16384) == 50.0
    assert hc2keelson.normalize_axis(-16384) == -50.0
    # Out-of-range values are clamped
    assert hc2keelson.normalize_axis(99999) == 100.0
    assert hc2keelson.normalize_axis(-99999) == -100.0


def test_joystick_event_unpack_layout():
    # 8 bytes: timestamp (uint32), value (int16), type (uint8), number (uint8)
    raw = struct.pack("IhBB", 42, -100, 0x02, 3)
    assert len(raw) == 8
    assert struct.unpack("IhBB", raw) == (42, -100, 0x02, 3)


def test_pubsub_key_pattern():
    # Locks the published key shape — flags any subject-rename regression.
    assert (
        keelson.construct_pubsub_key(
            "rise", "rov", "joystick_x_pct", "ssrov/joystick_x_pct"
        )
        == "rise/@v0/rov/pubsub/joystick_x_pct/ssrov/joystick_x_pct"
    )
    assert (
        keelson.construct_pubsub_key("rise", "rov", "button_state_change", "ssrov/arm")
        == "rise/@v0/rov/pubsub/button_state_change/ssrov/arm"
    )


def test_load_profile_by_name(hc2keelson):
    # Resolves "ssrov" → profiles/ssrov.yaml relative to the script
    profile = hc2keelson.load_profile("ssrov")
    assert profile["shift_button"] == 8
    assert profile["button_name_map"][13] == "arm"
    assert profile["shift_map"][0] == "input_hold_set"


def test_load_profile_by_path(hc2keelson, tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "axis_map:\n  0: joystick_x_pct\n"
        "button_name_map:\n  0: my_button\n"
    )
    profile = hc2keelson.load_profile(str(custom))
    assert profile["button_name_map"][0] == "my_button"
    # Optional keys get default values
    assert profile["shift_button"] is None
    assert profile["button_to_axis"] == {}
    assert profile["shift_map"] == {}


def test_load_profile_missing_required_keys(hc2keelson, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("axis_map:\n  0: joystick_x_pct\n")  # no button_name_map
    with pytest.raises(ValueError, match="missing required keys"):
        hc2keelson.load_profile(str(bad))
