#!/usr/bin/env python3

"""resolved_control -> RC override (crowsnest-dev#99): the connector applies a gate's message whole, or
sends nothing. Every expected PWM below is worked by hand: 1500 +- 500 at +-100 %."""

import argparse
from unittest.mock import MagicMock

import keelson
import pytest
from keelson.payloads.Fence_pb2 import Fence
from keelson.payloads.ResolvedControl_pb2 import (
    ControlState,
    ResolvedControl,
    ResolvedDof,
)
from keelson.interfaces.VehicleControl_pb2 import ControlAxis, ControlAxisMapping

import mavlink2keelson as m

ACTIVE = ControlState.CONTROL_STATE_ACTIVE
STALE = ControlState.CONTROL_STATE_STALE
TRANSITION = ControlState.CONTROL_STATE_TRANSITION
INHIBITED = ControlState.CONTROL_STATE_INHIBITED
UNMAPPED = ControlState.CONTROL_STATE_UNMAPPED

CHANNELS = {"steering": 1, "throttle": 3}


def config(routes=None, max_age_s=1.0):
    return m.ResolvedControlConfig(
        vessel_id="v1",
        gate_id="control-gate",
        max_age_s=max_age_s,
        routes=routes
        or (
            m.ResolvedRoute("steering", "rudder"),
            m.ResolvedRoute("throttle", "throttle"),
        ),
        channels=CHANNELS,
    )


def message(
    rudder=20.0,
    throttle=-40.0,
    *,
    term="T1",
    generation=1,
    sequence=1,
    states=None,
    **kw,
):
    states = states or {}
    dofs = []
    for name, value in (("rudder", rudder), ("throttle", throttle)):
        d = ResolvedDof(dof=name, control_state=states.get(name, ACTIVE))
        if value is not None:
            d.accepted_pct = value
        dofs.append(d)
    fields = {
        "vessel_id": "v1",
        "gate_id": "control-gate",
        "actuation_allowed": True,
        **kw,
    }
    return ResolvedControl(
        motion_epoch=Fence(term=term, generation=generation),
        sequence=sequence,
        dofs=dofs,
        **fields,
    )


def live_memory():
    """A memory that has heard T1 / generation 1 / sequence 1 just now."""
    mem = m.ResolvedControlMemory()
    channels, _ = m.resolved_control_channels(message(), config(), mem, 100.0)
    assert channels is None
    return mem


def apply(msg, mem=None, now=100.5, cfg=None):
    return m.resolved_control_channels(msg, cfg or config(), mem or live_memory(), now)[
        0
    ]


def test_a_good_message_becomes_the_exact_pwm_on_the_right_channels():
    assert apply(message(sequence=2)) == [1600, 0, 1300, 0, 0, 0, 0, 0]


def test_invert_and_unipolar_apply_per_route():
    routes = (
        m.ResolvedRoute("steering", "rudder", invert=True),
        m.ResolvedRoute("throttle", "throttle", unipolar=True),
    )
    assert apply(message(rudder=20, throttle=50, sequence=2), cfg=config(routes)) == [
        1400,
        0,
        1750,
        0,
        0,
        0,
        0,
        0,
    ]


@pytest.mark.parametrize("state", [ACTIVE, STALE, TRANSITION])
def test_the_gates_value_is_emitted_in_each_emitting_state(state):
    assert apply(message(sequence=2, states={"rudder": state})) is not None


@pytest.mark.parametrize(
    "state", [INHIBITED, UNMAPPED, ControlState.CONTROL_STATE_UNSPECIFIED]
)
def test_one_dof_that_may_not_emit_silences_them_all(state):
    assert apply(message(sequence=2, states={"rudder": state})) is None


def test_one_dof_with_nothing_accepted_silences_them_all():
    assert apply(message(rudder=None, sequence=2)) is None


def test_a_routed_dof_missing_from_the_message_silences_them_all():
    msg = message(sequence=2)
    del msg.dofs[1]
    assert apply(msg) is None


def test_a_non_finite_value_sends_nothing():
    assert apply(message(rudder=float("nan"), sequence=2)) is None


def test_actuation_not_allowed_sends_nothing():
    assert apply(message(sequence=2, actuation_allowed=False)) is None


def test_another_vessel_or_gate_sends_nothing_and_does_not_advance():
    mem = live_memory()
    assert apply(message(sequence=9, vessel_id="v2"), mem) is None
    assert apply(message(sequence=9, gate_id="impostor"), mem) is None
    assert mem.sequence == 1
    assert apply(message(sequence=2), mem) is not None


def test_a_missing_fence_sends_nothing():
    assert apply(message(term="", sequence=2)) is None


def test_a_repeated_or_older_sequence_sends_nothing():
    mem = live_memory()
    assert apply(message(sequence=1), mem) is None
    assert apply(message(sequence=0), mem) is None


def test_an_older_generation_sends_nothing_and_a_newer_one_does():
    mem = m.ResolvedControlMemory()
    apply(message(generation=5, sequence=1), mem, now=100.0)
    assert apply(message(generation=4, sequence=2), mem, now=100.1) is None
    assert apply(message(generation=5, sequence=3), mem, now=100.2) is not None
    assert apply(message(generation=6, sequence=4), mem, now=100.3) is not None


def test_a_new_term_is_accepted_with_its_restarted_sequence():
    mem = live_memory()
    assert (
        apply(message(term="T2", generation=1, sequence=1), mem, now=100.2) is not None
    )


def test_a_term_that_was_left_is_refused_for_good():
    mem = live_memory()
    apply(message(term="T2", sequence=1), mem, now=100.2)
    assert apply(message(term="T1", sequence=99), mem, now=100.3) is None
    assert mem.term == "T2"
    assert apply(message(term="T2", sequence=2), mem, now=100.4) is not None


def test_the_first_message_ever_moves_nothing():
    assert (
        m.resolved_control_channels(
            message(), config(), m.ResolvedControlMemory(), 100.0
        )[0]
        is None
    )


def test_exactly_at_the_dead_man_still_counts_and_past_it_does_not():
    assert apply(message(sequence=2), now=101.0) is not None
    mem = live_memory()
    assert apply(message(sequence=2), mem, now=101.001) is None
    assert (
        apply(message(sequence=3), mem, now=101.5) is not None
    ), "the stream proved itself again"


def test_the_dead_man_counts_a_message_that_may_not_emit():
    mem = live_memory()
    apply(message(sequence=2, actuation_allowed=False), mem, now=100.9)
    assert apply(message(sequence=3), mem, now=101.8) is not None


@pytest.mark.parametrize(
    "specs, routes",
    [
        (["steering=rudder"], (m.ResolvedRoute("steering", "rudder"),)),
        (
            ["throttle=main,invert,unipolar"],
            (m.ResolvedRoute("throttle", "main", True, True),),
        ),
    ],
)
def test_routes_parse(specs, routes):
    assert m.parse_resolved_routes(specs) == routes


@pytest.mark.parametrize(
    "spec", ["steering", "steering=", "yaw=rudder", "steering=rudder,backwards"]
)
def test_a_bad_route_is_refused(spec):
    with pytest.raises(ValueError):
        m.parse_resolved_routes([spec])


def test_an_axis_cannot_be_routed_twice():
    with pytest.raises(ValueError):
        m.parse_resolved_routes(["steering=a", "steering=b"])


BASE = [
    "-r",
    "rise",
    "-e",
    "v1",
    "-s",
    "mav",
    "--target-system",
    "1",
    "--mavlink-url",
    "udp:127.0.0.1:1",
]


@pytest.mark.parametrize(
    "extra",
    [
        ["--resolved-control-gate", "g"],
        ["--resolved-control-dof", "steering=rudder"],
        ["--resolved-control-gate", "g", "--resolved-control-dof", "nope=rudder"],
        [
            "--resolved-control-gate",
            "g",
            "--resolved-control-dof",
            "steering=rudder",
            "--resolved-control-max-age-s",
            "0",
        ],
    ],
)
def test_the_command_line_refuses_a_half_configured_mode(extra, monkeypatch, capsys):
    monkeypatch.setattr(m, "run", lambda args: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as exc:
        m.main(BASE + extra)
    assert exc.value.code == 2
    message = capsys.readouterr().err.split("error:")[-1]
    assert "resolved-control" in message or "unknown axis" in message


def test_it_is_off_by_default():
    args = m.build_arg_parser().parse_args(BASE)
    assert args.resolved_control_gate is None and args.resolved_control_dof == []


def test_a_complete_configuration_reaches_run(monkeypatch):
    monkeypatch.setattr(m, "run", lambda args: 7)
    assert (
        m.main(
            BASE
            + [
                "--resolved-control-gate",
                "g",
                "--resolved-control-dof",
                "steering=rudder",
            ]
        )
        == 7
    )


def make_input(max_age_s=1.0):
    session = MagicMock()
    args = argparse.Namespace(realm="rise", target_system=1)
    mav = MagicMock()
    return (
        m.ResolvedControlInput(session, args, mav, config(max_age_s=max_age_s)),
        session,
        mav,
    )


def test_the_input_subscribes_to_its_own_vessels_slot_and_sends_the_override():
    inp, session, mav = make_input()
    try:
        assert (
            session.declare_subscriber.call_args.args[0]
            == "rise/@v0/v1/pubsub/resolved_control/v1"
        )
        inp.apply(message(sequence=1), 100.0)
        assert not mav.mav.rc_channels_override_send.called
        inp.apply(message(sequence=2), 100.1)
        mav.mav.rc_channels_override_send.assert_called_once_with(
            1, 0, 1600, 0, 1300, 0, 0, 0, 0, 0
        )
    finally:
        inp.close()


def test_a_sample_on_the_wire_is_decoded_and_applied():
    inp, _, mav = make_input()
    try:
        for seq in (1, 2):
            sample = MagicMock()
            sample.payload.to_bytes.return_value = keelson.enclose(
                message(sequence=seq).SerializeToString()
            )
            inp._on_sample(sample)
        assert mav.mav.rc_channels_override_send.call_count == 1
    finally:
        inp.close()


def test_a_garbled_sample_sends_nothing():
    inp, _, mav = make_input()
    try:
        sample = MagicMock()
        sample.payload.to_bytes.return_value = b"\xff"
        inp._on_sample(sample)
        assert not mav.mav.rc_channels_override_send.called
    finally:
        inp.close()


def test_silence_sends_no_release_frame_and_is_reported_once(caplog):
    inp, _, mav = make_input()
    try:
        inp.apply(message(sequence=1), 100.0)
        inp.apply(message(sequence=2), 100.1)
        with caplog.at_level("WARNING"):
            inp.check_silence(101.2)
            inp.check_silence(102.0)
        assert mav.mav.rc_channels_override_send.call_count == 1
        assert (
            len([r for r in caplog.records if "silent past the dead-man" in r.message])
            == 1
        )
    finally:
        inp.close()


@pytest.mark.parametrize("channel", [None, 0, 9])
def test_an_axis_without_a_usable_channel_is_refused(channel):
    cfg = m.ResolvedControlConfig(
        "v1", "g", 1.0, (m.ResolvedRoute("steering", "rudder"),), {"steering": channel}
    )
    with pytest.raises(ValueError):
        m.ResolvedControlInput(
            MagicMock(),
            argparse.Namespace(realm="rise", target_system=1),
            MagicMock(),
            cfg,
        )


def mapping_request():
    return ControlAxisMapping(axes={"steering": ControlAxis(subject="joystick_x_pct")})


def test_set_control_mapping_refuses_axes_while_the_gate_drives():
    op = MagicMock()
    op.request_bytes = mapping_request().SerializeToString()
    args = argparse.Namespace(_resolved_control_input=object())
    m._handle_set_control_mapping(MagicMock(), args, op, 1)
    assert "takes no axes" in op.reply_err.call_args.args[0]
    assert not op.query.reply.called


def test_an_empty_mapping_is_still_allowed_while_the_gate_drives(monkeypatch):
    monkeypatch.setattr(m, "_confirmed_sysid_mygcs_mismatch", lambda *a: None)
    op = MagicMock()
    op.request_bytes = ControlAxisMapping().SerializeToString()
    state = MagicMock()
    args = argparse.Namespace(
        _resolved_control_input=object(), _control_axis_state=state, source_system=255
    )
    m._handle_set_control_mapping(MagicMock(), args, op, 1)
    assert not op.reply_err.called
    state.set_mapping.assert_called_once()
