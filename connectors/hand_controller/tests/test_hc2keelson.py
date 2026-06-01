"""Sanity tests that catch subject/protocol regressions."""

import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

import keelson
import pytest


# ---------------------------------------------------------------------------
# Module-state cleanup. The connector module is session-scoped (loaded once
# via SourceFileLoader in conftest), so PUBLISHERS, _axis_last_published, and
# _axis_last_known persist across tests unless we clear them.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_module_state(hc2keelson):
    hc2keelson.PUBLISHERS.clear()
    hc2keelson._axis_last_published.clear()
    hc2keelson._axis_last_known.clear()
    hc2keelson._shift_held = False
    yield
    hc2keelson.PUBLISHERS.clear()
    hc2keelson._axis_last_published.clear()
    hc2keelson._axis_last_known.clear()
    hc2keelson._shift_held = False


# ---------------------------------------------------------------------------
# Test scaffolding helpers.
# ---------------------------------------------------------------------------
def _make_args(**overrides):
    """Build an argparse-compatible args namespace with sensible defaults."""
    defaults = dict(
        realm="rise",
        entity_id="rov",
        axis_center_snap_pct=0.0,
        axis_min_hz=10.0,
        axis_max_hz=50.0,
        axis_deadband_pct=1.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_session():
    """Mock zenoh session whose publishers record every put() call."""
    session = MagicMock()
    publishers = {}

    def declare_publisher(key_expr, **kwargs):
        pub = MagicMock()
        pub.put_calls = []
        pub.put = MagicMock(side_effect=lambda data: pub.put_calls.append(data))
        publishers[key_expr] = pub
        return pub

    session.declare_publisher = MagicMock(side_effect=declare_publisher)
    session.publishers = publishers
    return session


def _profile():
    return {
        "axis_map": {0: "joystick_x_pct", 1: "joystick_y_pct"},
        "button_name_map": {8: "shift", 13: "arm", 0: "grip_open"},
        "button_to_axis": {},
        "shift_button": 8,
        "shift_map": {13: "input_hold_set"},
    }


# ---------------------------------------------------------------------------
# Existing regression tests.
# ---------------------------------------------------------------------------
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
        "axis_map:\n  0: joystick_x_pct\nbutton_name_map:\n  0: my_button\n"
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


# ---------------------------------------------------------------------------
# INIT semantics — axes treat INIT as a normal event; buttons drop it.
# ---------------------------------------------------------------------------
def test_init_axis_event_is_published(hc2keelson):
    """The kernel's bootstrap snapshot for an axis must reach the bus —
    otherwise late joiners stay blind until the operator wiggles the stick."""
    session = _make_session()
    args = _make_args()
    hc2keelson.handle_joystick_event(
        timestamp=0,
        value=16384,  # 50%
        event_type=hc2keelson.JS_EVENT_AXIS | hc2keelson.JS_EVENT_INIT,
        number=0,
        session=session,
        args=args,
        profile=_profile(),
        source_base="ssrov",
    )
    # Exactly one publisher declared, exactly one put() call against it.
    assert len(session.publishers) == 1
    pub = next(iter(session.publishers.values()))
    assert len(pub.put_calls) == 1
    # Backstop state seeded with the INIT value.
    assert "ssrov/joystick_x_pct" in hc2keelson._axis_last_known
    _, value = hc2keelson._axis_last_known["ssrov/joystick_x_pct"]
    assert value == 50.0


def test_init_button_event_is_suppressed(hc2keelson):
    """INIT button events would otherwise fire spurious press/release events
    at startup based on whatever the operator happened to be touching."""
    session = _make_session()
    args = _make_args()
    hc2keelson.handle_joystick_event(
        timestamp=0,
        value=1,
        event_type=hc2keelson.JS_EVENT_BUTTON | hc2keelson.JS_EVENT_INIT,
        number=13,  # arm
        session=session,
        args=args,
        profile=_profile(),
        source_base="ssrov",
    )
    assert session.publishers == {}


def test_init_button_does_not_mutate_shift_state(hc2keelson):
    """Even if the operator's thumb is on the shift button when the device
    opens, the resulting INIT event must not enter the modifier state — or
    every subsequent button press would resolve under its shifted name."""
    session = _make_session()
    args = _make_args()
    # INIT shift-button "pressed".
    hc2keelson.handle_joystick_event(
        timestamp=0,
        value=1,
        event_type=hc2keelson.JS_EVENT_BUTTON | hc2keelson.JS_EVENT_INIT,
        number=8,  # the shift button per _profile()
        session=session,
        args=args,
        profile=_profile(),
        source_base="ssrov",
    )
    assert hc2keelson._shift_held is False
    # A normal arm press should now publish under "arm", not "input_hold_set".
    hc2keelson.handle_joystick_event(
        timestamp=0,
        value=1,
        event_type=hc2keelson.JS_EVENT_BUTTON,
        number=13,
        session=session,
        args=args,
        profile=_profile(),
        source_base="ssrov",
    )
    assert any("ssrov/arm" in key for key in session.publishers), session.publishers


# ---------------------------------------------------------------------------
# Rate-limit-aware state tracking + backstop tick.
# ---------------------------------------------------------------------------
def test_axis_last_known_updated_on_rate_limited_event(hc2keelson):
    """A change-driven event that the rate limiter suppresses must still
    update _axis_last_known — otherwise the backstop publishes stale data."""
    session = _make_session()
    # Tight cap so the second event is suppressed even at identical timestamps.
    args = _make_args(axis_max_hz=1.0, axis_deadband_pct=10.0)

    hc2keelson.handle_joystick_event(
        timestamp=0,
        value=10000,
        event_type=hc2keelson.JS_EVENT_AXIS,
        number=0,
        session=session,
        args=args,
        profile=_profile(),
        source_base="ssrov",
    )
    first_publishes = len(next(iter(session.publishers.values())).put_calls)
    assert first_publishes == 1

    # Tiny delta within the deadband, within the rate cap window.
    hc2keelson.handle_joystick_event(
        timestamp=0,
        value=10100,  # ~0.3 pct delta, below 10.0 deadband
        event_type=hc2keelson.JS_EVENT_AXIS,
        number=0,
        session=session,
        args=args,
        profile=_profile(),
        source_base="ssrov",
    )
    # Suppressed: same publisher, no new put().
    assert len(next(iter(session.publishers.values())).put_calls) == 1

    # But _axis_last_known reflects the newer, suppressed value.
    _, latest_value = hc2keelson._axis_last_known["ssrov/joystick_x_pct"]
    assert abs(latest_value - hc2keelson.normalize_axis(10100)) < 1e-9


def test_axis_backstop_tick_publishes_every_known_axis(hc2keelson):
    """The backstop must publish a value for every axis it has ever seen,
    not just the ones moved recently."""
    session = _make_session()
    args = _make_args()
    # Seed two axes via the event handler so source_id encoding is realistic.
    for axis_num, value in ((0, 16384), (1, -8192)):
        hc2keelson.handle_joystick_event(
            timestamp=0,
            value=value,
            event_type=hc2keelson.JS_EVENT_AXIS,
            number=axis_num,
            session=session,
            args=args,
            profile=_profile(),
            source_base="ssrov",
        )

    # Clear the event-driven publishes so we can count backstop output cleanly.
    for pub in session.publishers.values():
        pub.put_calls.clear()

    hc2keelson._axis_backstop_tick(session, args, now_ns=123_000_000_000)

    # One publish per known axis.
    publishes = sum(len(p.put_calls) for p in session.publishers.values())
    assert publishes == 2
    # Both expected subject keys are present.
    keys = set(session.publishers.keys())
    assert any("joystick_x_pct" in k for k in keys)
    assert any("joystick_y_pct" in k for k in keys)


def test_terminal_inputs_rejects_min_above_max(hc2keelson, monkeypatch):
    """Cross-flag validation: --axis-min-hz > --axis-max-hz is a config error."""
    monkeypatch.setattr(
        "sys.argv",
        ["hc2keelson", "--axis-min-hz", "100", "--axis-max-hz", "50"],
    )
    with pytest.raises(SystemExit):
        hc2keelson.terminal_inputs()


def test_terminal_inputs_allows_zero_bounds(hc2keelson, monkeypatch):
    """Zero on either side disables that bound and skips validation."""
    monkeypatch.setattr(
        "sys.argv",
        ["hc2keelson", "--axis-min-hz", "0", "--axis-max-hz", "50"],
    )
    args = hc2keelson.terminal_inputs()
    assert args.axis_min_hz == 0
    assert args.axis_max_hz == 50

    monkeypatch.setattr(
        "sys.argv",
        ["hc2keelson", "--axis-min-hz", "100", "--axis-max-hz", "0"],
    )
    args = hc2keelson.terminal_inputs()
    assert args.axis_min_hz == 100
    assert args.axis_max_hz == 0


def test_axis_max_hz_zero_disables_rate_cap(hc2keelson):
    """--axis-max-hz 0 means no upper bound: every change-driven event publishes
    regardless of how close together they arrive or how small the delta."""
    session = _make_session()
    args = _make_args(axis_max_hz=0.0)

    for v in (10000, 10001, 10002):
        hc2keelson.handle_joystick_event(
            timestamp=0,
            value=v,
            event_type=hc2keelson.JS_EVENT_AXIS,
            number=0,
            session=session,
            args=args,
            profile=_profile(),
            source_base="ssrov",
        )

    pub = next(iter(session.publishers.values()))
    assert len(pub.put_calls) == 3
