"""Tests for the well-known QoS profiles (keelson.qos) and the Zenoh adapter
(keelson.scaffolding.qos_zenoh)."""

from unittest.mock import MagicMock


import keelson
from keelson import qos
from keelson.scaffolding.qos_zenoh import (
    declare_publisher,
    declare_publisher_for_subject,
    put,
    zenoh_publisher_kwargs,
    _PRIORITY,
    _CONGESTION_CONTROL,
    _RELIABILITY,
)


# ---------------------------------------------------------------------------
# qos.yaml integrity (consistency with subjects.yaml + the profile vocabulary)
# ---------------------------------------------------------------------------
def test_qos_yaml_was_loaded():
    # If generate_python.sh hasn't copied qos.yaml, every subject silently
    # falls back to the default — assert the real profiles are present.
    assert qos._PROFILES, "qos.yaml profiles were not loaded"
    assert "default" in qos._PROFILES


def test_default_profile_is_defined():
    assert qos._DEFAULT_PROFILE in qos._PROFILES


def test_every_assigned_subject_is_well_known():
    # A QoS assignment for a subject that doesn't exist in subjects.yaml is a
    # typo / stale entry — it would just never match anything at runtime.
    unknown = [s for s in qos._SUBJECT_PROFILES if not keelson.is_subject_well_known(s)]
    assert not unknown, f"qos.yaml assigns QoS to unknown subjects: {unknown}"


def test_every_assignment_references_a_defined_profile():
    bad = {s: p for s, p in qos._SUBJECT_PROFILES.items() if p not in qos._PROFILES}
    assert not bad, f"qos.yaml assigns undefined profiles: {bad}"


def test_all_profiles_have_valid_vocabulary():
    for profile in qos._PROFILES.values():
        assert profile.priority in qos._PRIORITIES
        assert profile.congestion_control in qos._CONGESTION_CONTROLS
        assert profile.reliability in qos._RELIABILITIES
        assert isinstance(profile.express, bool)


# ---------------------------------------------------------------------------
# qos_for() resolution
# ---------------------------------------------------------------------------
def test_qos_for_assigned_subject():
    # location_fix is assigned the elevated profile in the bundled qos.yaml.
    profile = qos.qos_for("location_fix")
    assert profile.name == "elevated"
    assert profile.priority == "DATA_HIGH"


def test_qos_for_transient_is_best_effort():
    profile = qos.qos_for("radar_spoke")
    assert profile.name == "transient"
    assert profile.reliability == "BEST_EFFORT"


def test_qos_for_control_inputs_are_realtime():
    # The hand_controller inversion fix: control inputs outrank transient frames.
    for subject in ("joystick_x_pct", "button_state_change", "wheel_position_pct"):
        assert qos.qos_for(subject).name == "realtime"
        assert qos.qos_for(subject).priority == "REAL_TIME"


def test_safety_critical_data_is_elevated():
    # Collision-avoidance metrics and externally-authored alerts ride elevated.
    for subject in ("target_cpa_m", "target_tcpa_s", "weather_alert"):
        assert qos.qos_for(subject).name == "elevated"


def test_rtcm_corrections_are_not_backgrounded():
    # RTK corrections feed positioning; they must not sit at lowest priority.
    assert qos.qos_for("raw_rtcm_v3").name == "default"


def test_qos_for_unlisted_subject_falls_back_to_default():
    profile = qos.qos_for("a_subject_that_does_not_exist")
    assert profile.name == qos._DEFAULT_PROFILE


# ---------------------------------------------------------------------------
# Zenoh adapter: every profile maps to real zenoh enums
# ---------------------------------------------------------------------------
def test_enum_maps_cover_the_vocabulary():
    # Guards against a new profile value that the adapter can't translate.
    assert qos._PRIORITIES <= set(_PRIORITY)
    assert qos._CONGESTION_CONTROLS <= set(_CONGESTION_CONTROL)
    assert qos._RELIABILITIES <= set(_RELIABILITY)


def test_zenoh_publisher_kwargs_for_every_profile():
    import zenoh

    for name, profile in qos._PROFILES.items():
        kwargs = zenoh_publisher_kwargs(profile)
        assert isinstance(kwargs["priority"], zenoh.Priority), name
        assert isinstance(kwargs["congestion_control"], zenoh.CongestionControl), name
        assert isinstance(kwargs["reliability"], zenoh.Reliability), name
        assert isinstance(kwargs["express"], bool), name


def test_zenoh_publisher_kwargs_accepts_subject_name():
    kwargs = zenoh_publisher_kwargs("location_fix")
    import zenoh

    assert kwargs["priority"] == zenoh.Priority.DATA_HIGH
    assert kwargs["congestion_control"] == zenoh.CongestionControl.DROP
    assert kwargs["reliability"] == zenoh.Reliability.RELIABLE
    assert kwargs["express"] is False


def test_publisher_kwargs_are_accepted_by_real_declare_publisher():
    # Guards against passing a kwarg the real zenoh API rejects (mocks can't).
    import inspect
    import zenoh

    accepted = set(inspect.signature(zenoh.Session.declare_publisher).parameters)
    assert set(zenoh_publisher_kwargs("location_fix")) <= accepted


def test_put_kwargs_are_accepted_by_real_session_put():
    # session.put() does NOT accept `reliability` (unlike declare_publisher) —
    # this is what crashed the put()-style connectors at runtime. Validate the
    # kwargs put() forwards against the real signature.
    import inspect
    import zenoh

    session = MagicMock()
    put(session, keelson.construct_pubsub_key("rise", "boat", "location_fix", "g0"), b"x")

    _, called_kwargs = session.put.call_args
    accepted = set(inspect.signature(zenoh.Session.put).parameters)
    assert "reliability" not in called_kwargs
    assert set(called_kwargs) <= accepted


# ---------------------------------------------------------------------------
# declare_publisher: derives the subject from the key and applies QoS
# ---------------------------------------------------------------------------
def test_declare_publisher_derives_qos_from_key():
    import zenoh

    session = MagicMock()
    key = keelson.construct_pubsub_key("rise", "boat", "radar_spoke", "radar0")

    declare_publisher(session, key)

    session.declare_publisher.assert_called_once()
    called_key, called_kwargs = session.declare_publisher.call_args
    assert called_key[0] == key
    # radar_spoke -> transient
    assert called_kwargs["priority"] == zenoh.Priority.INTERACTIVE_HIGH
    assert called_kwargs["reliability"] == zenoh.Reliability.BEST_EFFORT


def test_declare_publisher_override_wins():
    import zenoh

    session = MagicMock()
    key = keelson.construct_pubsub_key("rise", "boat", "location_fix", "gnss0")

    declare_publisher(session, key, congestion_control=zenoh.CongestionControl.BLOCK)

    _, called_kwargs = session.declare_publisher.call_args
    assert called_kwargs["congestion_control"] == zenoh.CongestionControl.BLOCK
    # non-overridden fields still come from the profile
    assert called_kwargs["priority"] == zenoh.Priority.DATA_HIGH


def test_declare_publisher_for_subject_explicit():
    import zenoh

    session = MagicMock()
    declare_publisher_for_subject(session, "some/key", "image_compressed")

    _, called_kwargs = session.declare_publisher.call_args
    assert called_kwargs["priority"] == zenoh.Priority.INTERACTIVE_HIGH


def test_declare_publisher_unparseable_key_uses_default(caplog):

    session = MagicMock()
    declare_publisher(session, "not-a-keelson-key")

    _, called_kwargs = session.declare_publisher.call_args
    # Falls back to the default profile rather than raising.
    default = qos.qos_for("")
    assert called_kwargs["priority"] == _PRIORITY[default.priority]
