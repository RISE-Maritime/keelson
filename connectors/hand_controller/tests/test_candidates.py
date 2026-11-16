"""The hand controller behind a motion gate (crowsnest-dev#95): ControlCandidate per mapped axis,
shaped under the profile's calibration, on a stream drawn afresh on every (re)connect, with the
device's own health from its read watchdog, and nothing at all from a dead source."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import keelson
import pytest
from keelson.payloads.ControlCandidate_pb2 import ControlCandidate
from keelson.payloads.EntityHealth_pb2 import HealthLevel

PROFILE = {
    "axis_map": {0: "joystick_x_pct", 1: "joystick_y_pct", 2: "joystick_z_pct"},
    "button_name_map": {0: "arm"},
    "button_to_axis": {6: "joystick_lt_pct"},
    "shift_button": None,
    "shift_map": {},
    "candidates": {
        "version": "test-helm/3",
        "dofs": {
            "joystick_x_pct": {"dof": "rudder", "deadband_pct": 10.0},
            "joystick_y_pct": {"dof": "throttle", "invert": True},
        },
    },
}


class Clock:
    def __init__(self):
        self.t = 10**12

    def __call__(self):
        return self.t


@pytest.fixture
def hc(hc2keelson, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(hc2keelson, "_mono", clock)
    hc2keelson.PUBLISHERS.clear()
    hc2keelson._axis_last_published.clear()
    hc2keelson._axis_last_known.clear()
    hc2keelson._source_alive = False
    hc2keelson._candidate_plan = hc2keelson.candidate_plan(
        PROFILE, "ROC-1", "helm-1", "free_running"
    )
    hc2keelson.clock = clock
    yield hc2keelson
    hc2keelson._candidate_plan = None
    hc2keelson._source_alive = False
    hc2keelson.PUBLISHERS.clear()
    hc2keelson._axis_last_published.clear()
    hc2keelson._axis_last_known.clear()


def session():
    s = MagicMock()
    s.publishers = {}

    def declare(key_expr, **kw):
        pub = MagicMock()
        pub.put_calls = []
        pub.put = MagicMock(side_effect=lambda data: pub.put_calls.append(data))
        s.publishers[key_expr] = pub
        return pub

    s.declare_publisher = MagicMock(side_effect=declare)
    return s


def args():
    return SimpleNamespace(
        realm="rise",
        entity_id="roc1",
        axis_center_snap_pct=0.0,
        axis_min_hz=10.0,
        axis_max_hz=0.0,
        axis_deadband_pct=1.0,
    )


def candidates(s):
    out = []
    for key, pub in s.publishers.items():
        if "/control_candidate/" in key:
            out += [
                (key, ControlCandidate.FromString(keelson.uncover(d)[2]))
                for d in pub.put_calls
            ]
    return out


def move(hc, s, number, raw, a=None):
    hc.handle_joystick_event(
        0, raw, hc.JS_EVENT_AXIS, number, s, a or args(), PROFILE, "test"
    )


# ---------------------------------------------------------------------------- shaping


@pytest.mark.parametrize(
    "rule,raw,want",
    [
        ({}, 42.0, 42.0),
        ({"invert": True}, 42.0, -42.0),
        ({"deadband_pct": 10.0}, 9.0, 0.0),
        ({"deadband_pct": 10.0}, 55.0, 50.0),  # re-scaled: 10 -> 0, 100 stays 100
        ({"deadband_pct": 10.0}, 100.0, 100.0),
        ({"expo": 1.0}, 50.0, 12.5),  # pure cubic
        ({"expo": 0.5}, -50.0, -31.25),
        ({"scale": 0.5}, 80.0, 40.0),
        ({"scale": 2.0}, 80.0, 100.0),  # clamped
        ({}, 140.0, 100.0),  # a driver overshoot is clamped first
        ({"invert": True, "deadband_pct": 20.0}, 60.0, -50.0),  # invert before deadband
    ],
)
def test_shape_is_invert_deadband_expo_scale_clamp(hc, rule, raw, want):
    assert hc.shape(raw, rule) == pytest.approx(want)


# ---------------------------------------------------------------------------- the candidate


def test_a_mapped_axis_becomes_a_candidate_for_its_dof(hc):
    hc._source_up()
    s = session()
    move(hc, s, 0, 16384)  # 50 %
    move(hc, s, 1, 16384)
    got = {c.dof: (k, c) for k, c in candidates(s)}
    key, rudder = got["rudder"]
    assert key == keelson.construct_pubsub_key(
        "rise", "roc1", "control_candidate", "ROC-1/helm-1/rudder"
    )
    assert rudder.source == "ROC-1/helm-1/rudder"
    assert rudder.value_pct == pytest.approx((50.0 - 10.0) / 90.0 * 100.0)
    assert got["throttle"][1].value_pct == pytest.approx(-50.0)  # inverted
    assert rudder.profile_version == "test-helm/3"
    assert rudder.device_health == HealthLevel.HEALTH_NOMINAL
    assert rudder.stream.clock_quality == 2 and rudder.HasField("timestamp")


def test_an_unmapped_axis_is_never_a_candidate_and_the_legacy_axes_are_unchanged(hc):
    hc._source_up()
    s = session()
    move(hc, s, 2, 16384)
    assert candidates(s) == []
    assert any(k.endswith("/joystick_z_pct/test/joystick_z_pct") for k in s.publishers)
    move(hc, s, 0, 16384)
    legacy = [k for k in s.publishers if "/joystick_x_pct/" in k]
    assert legacy == [
        keelson.construct_pubsub_key(
            "rise", "roc1", "joystick_x_pct", "test/joystick_x_pct"
        )
    ]


def test_without_the_logical_ids_or_the_block_there_are_no_candidates(hc2keelson):
    assert hc2keelson.candidate_plan(PROFILE, None, None, "free_running") is None
    assert hc2keelson.candidate_plan(PROFILE, "ROC-1", None, "free_running") is None
    plain = {k: v for k, v in PROFILE.items() if k != "candidates"}
    assert hc2keelson.candidate_plan(plain, "ROC-1", "helm-1", "free_running") is None


# ---------------------------------------------------------------------------- the stream


def test_one_rising_sequence_per_source_and_a_new_stream_on_every_reconnect(hc):
    hc._source_up()
    s = session()
    for raw in (1000, 2000, 3000):
        move(hc, s, 0, raw)
    move(hc, s, 1, 1000)
    first = candidates(s)
    rudder = [c for _, c in first if c.dof == "rudder"]
    assert [c.stream.sequence for c in rudder] == [1, 2, 3]
    assert [c.stream.sequence for _, c in first if c.dof == "throttle"] == [1]
    stream = rudder[0].stream.stream_id
    assert len(stream) == 32 and {c.stream.stream_id for _, c in first} == {stream}

    hc._source_alive = False  # unplugged ...
    hc._source_up()  # ... and back: a new incarnation the gate must interlock
    hc.PUBLISHERS.clear()  # the connector caches publishers per key; a fresh mock session
    s2 = session()
    move(hc, s2, 0, 1000)
    ((_, again),) = candidates(s2)
    assert again.stream.stream_id != stream and again.stream.sequence == 1


def test_a_dead_source_publishes_no_candidate_even_from_an_event(hc):
    hc._source_alive = False
    s = session()
    move(hc, s, 0, 16384)
    assert candidates(s) == []


def test_the_backstop_republishes_candidates_and_stops_with_the_source(hc):
    hc._source_up()
    s = session()
    move(hc, s, 0, 16384)
    n = len(candidates(s))
    hc._axis_backstop_tick(s, args(), 123)
    assert len(candidates(s)) == n + 1
    hc._source_alive = False
    hc._axis_backstop_tick(s, args(), 124)
    assert len(candidates(s)) == n + 1


def test_a_digital_trigger_mapped_to_a_dof_is_a_candidate(hc, monkeypatch):
    profile = {
        **PROFILE,
        "candidates": {"version": "v", "dofs": {"joystick_lt_pct": {"dof": "horn"}}},
    }
    hc._candidate_plan = hc.candidate_plan(profile, "ROC-1", "helm-1", "synchronised")
    hc._source_up()
    s = session()
    hc.handle_joystick_event(0, 1, hc.JS_EVENT_BUTTON, 6, s, args(), profile, "test")
    ((_, c),) = candidates(s)
    assert (c.dof, c.value_pct, c.stream.clock_quality) == ("horn", 100.0, 1)


# ---------------------------------------------------------------------------- t1 and t2 (crowsnest-dev#98)


class WallClock:
    """time.time_ns that advances 1 ms on every read, so each stamp names the moment it was taken."""

    def __init__(self):
        self.t = 1_700_000_000_000_000_000
        self.reads = []

    def __call__(self):
        self.t += 1_000_000
        self.reads.append(self.t)
        return self.t


def test_the_sample_time_is_the_read_and_the_envelope_is_the_publish(hc, monkeypatch):
    wall = WallClock()
    monkeypatch.setattr(hc.time, "time_ns", wall)
    hc._source_up()
    s = session()
    move(hc, s, 0, 16384)
    read = wall.reads[0]  # the handler's first stamp: the event was just read
    ((_, c),) = candidates(s)
    data = s.publishers[
        next(k for k in s.publishers if "/control_candidate/" in k)
    ].put_calls[0]
    _, enclosed_at, _ = keelson.uncover(data)
    assert c.timestamp.ToNanoseconds() == read
    assert (
        enclosed_at > read and enclosed_at in wall.reads
    )  # a later stamp: taken as it went on the bus


def test_a_backstop_sample_is_stamped_with_its_own_time(hc, monkeypatch):
    monkeypatch.setattr(hc.time, "time_ns", WallClock())
    hc._source_up()
    s = session()
    move(hc, s, 0, 16384)
    hc._axis_backstop_tick(s, args(), 5_000_000_000_000_000_000)
    *_, (_, again) = candidates(s)
    assert again.timestamp.ToNanoseconds() == 5_000_000_000_000_000_000


# ---------------------------------------------------------------------------- the read watchdog


def test_degraded_after_the_read_budget_and_nominal_again_after_a_read(hc):
    hc._source_up()
    s = session()
    hc.clock.t += hc.READ_WATCHDOG_NS + 1
    move(hc, s, 0, 16384)
    hc._mark_read()
    move(hc, s, 0, 20000)
    health = [c.device_health for _, c in candidates(s)]
    assert health == [HealthLevel.HEALTH_DEGRADED, HealthLevel.HEALTH_NOMINAL]
    # Degraded is still published: validity is the gate's to decide.


def test_within_the_budget_it_is_nominal(hc):
    hc._source_up()
    hc.clock.t += hc.READ_WATCHDOG_NS
    assert hc.device_health() == HealthLevel.HEALTH_NOMINAL


# ---------------------------------------------------------------------------- the profile


@pytest.mark.parametrize(
    "block,why",
    [
        ({"dofs": {"joystick_x_pct": {"dof": "rudder"}}}, "needs a version"),
        ({"version": "v", "dofs": {}}, "needs dofs"),
        (
            {"version": "v", "dofs": {"wheel_pct": {"dof": "rudder"}}},
            "not an axis of this profile",
        ),
        ({"version": "v", "dofs": {"joystick_x_pct": {}}}, "names no dof"),
        (
            {
                "version": "v",
                "dofs": {
                    "joystick_x_pct": {"dof": "r"},
                    "joystick_y_pct": {"dof": "r"},
                },
            },
            "driven twice",
        ),
        (
            {"version": "v", "dofs": {"joystick_x_pct": {"dof": "r", "gain": 2}}},
            "unknown key",
        ),
        (
            {
                "version": "v",
                "dofs": {"joystick_x_pct": {"dof": "r", "deadband_pct": 100}},
            },
            "deadband_pct",
        ),
        (
            {"version": "v", "dofs": {"joystick_x_pct": {"dof": "r", "expo": 1.5}}},
            "expo",
        ),
    ],
)
def test_the_candidates_block_is_validated(hc2keelson, block, why):
    profile = {**PROFILE, "candidates": block}
    with pytest.raises(ValueError, match=why):
        hc2keelson._validate_profile(dict(profile))


def test_a_profile_without_the_block_still_loads(hc2keelson):
    plain = {k: v for k, v in PROFILE.items() if k != "candidates"}
    assert hc2keelson._validate_profile(dict(plain))["axis_map"] == PROFILE["axis_map"]


@pytest.mark.parametrize(
    "argv,why",
    [
        (["--site", "ROC-1"], "go together"),
        (["--site", "ROC/1", "--device-id", "helm-1"], "one logical key chunk"),
        (["--site", "ROC-1", "--device-id", "/dev/input/js0"], "one logical key chunk"),
    ],
)
def test_the_logical_ids_are_checked(hc2keelson, monkeypatch, argv, why, capsys):
    monkeypatch.setattr("sys.argv", ["hc2keelson", "--entity-id", "hil", *argv])
    with pytest.raises(SystemExit):
        hc2keelson.terminal_inputs()
    assert why in capsys.readouterr().err


@pytest.mark.parametrize("name", ["ssrov", "logitech"])
def test_the_bundled_example_block_is_a_valid_one_once_uncommented(hc2keelson, name):
    import pathlib

    import yaml

    path = (
        pathlib.Path(hc2keelson.__file__).resolve().parent.parent
        / "profiles"
        / f"{name}.yaml"
    )
    text = path.read_text()
    head, example = text.split("# candidates:", 1)
    uncommented = (
        head
        + "candidates:"
        + "\n".join(
            line[2:] if line.startswith("# ") else line for line in example.splitlines()
        )
    )
    profile = hc2keelson._validate_profile(yaml.safe_load(uncommented))
    plan = hc2keelson.candidate_plan(profile, "ROC-1", "helm-1", "free_running")
    assert {r["dof"] for r in plan["rules"].values()} == {"rudder", "throttle"}
