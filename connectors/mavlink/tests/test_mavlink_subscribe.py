"""Unit tests for the pymavlink message_hooks subscribe() helper.

The helper is the foundation for migrating RPC handlers away from
calling ``mav.recv_match`` directly. These tests exercise it against
a FakeMav that fires hooks deterministically — no real socket, no
real timing.
"""

import queue
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import mavlink2keelson


# ---------------------------------------------------------------------------
# FakeMav: just enough surface to fire message_hooks
# ---------------------------------------------------------------------------


class FakeMav:
    """Minimal stand-in for pymavlink mavlink_connection.

    Owns a ``message_hooks`` list (matching pymavlink's attribute name)
    and exposes a ``fire(msg)`` method that iterates the hooks the same
    way pymavlink's ``post_message`` does — over a live, uncopied list.
    """

    def __init__(self):
        self.message_hooks = []

    def fire(self, msg):
        for hook in self.message_hooks:
            hook(self, msg)


def _msg(msg_type: str, **fields):
    """Build a fake mavlink message with ``get_type()`` + named fields."""
    ns = SimpleNamespace(**fields)
    ns.get_type = lambda: msg_type
    return ns


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_subscribe_installs_and_removes_hook():
    mav = FakeMav()
    assert mav.message_hooks == []

    with mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",)):
        assert len(mav.message_hooks) == 1

    assert mav.message_hooks == []


def test_subscribe_removes_hook_on_exception():
    mav = FakeMav()
    with pytest.raises(RuntimeError):
        with mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",)):
            assert len(mav.message_hooks) == 1
            raise RuntimeError("handler died mid-wait")
    assert mav.message_hooks == []


def test_subscribe_double_exit_is_idempotent():
    mav = FakeMav()
    sub = mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",))
    sub.__enter__()
    sub.__exit__(None, None, None)
    # Second exit must not raise even though the hook is already gone.
    sub.__exit__(None, None, None)
    assert mav.message_hooks == []


# ---------------------------------------------------------------------------
# Filtering: types + predicate
# ---------------------------------------------------------------------------


def test_type_filter_keeps_only_matching_frames():
    mav = FakeMav()
    with mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",)) as sub:
        mav.fire(_msg("HEARTBEAT"))
        mav.fire(_msg("COMMAND_ACK", command=42, result=0))
        mav.fire(_msg("ATTITUDE"))
        mav.fire(_msg("COMMAND_ACK", command=43, result=4))
    assert sub.queue.qsize() == 2
    got = [sub.queue.get_nowait(), sub.queue.get_nowait()]
    assert [m.command for m in got] == [42, 43]


def test_predicate_runs_only_on_type_match():
    mav = FakeMav()
    predicate_calls = []

    def predicate(m):
        predicate_calls.append(m.get_type())
        return m.command == 99

    with mavlink2keelson.subscribe(
        mav, types=("COMMAND_ACK",), predicate=predicate
    ) as sub:
        mav.fire(_msg("HEARTBEAT"))  # predicate must NOT be called for this
        mav.fire(_msg("COMMAND_ACK", command=1))
        mav.fire(_msg("COMMAND_ACK", command=99))

    assert predicate_calls == ["COMMAND_ACK", "COMMAND_ACK"]
    assert sub.queue.qsize() == 1
    assert sub.queue.get_nowait().command == 99


def test_no_types_means_all_frames_match():
    mav = FakeMav()
    with mavlink2keelson.subscribe(mav) as sub:
        mav.fire(_msg("HEARTBEAT"))
        mav.fire(_msg("ATTITUDE"))
    assert sub.queue.qsize() == 2


def test_concurrent_waiters_only_see_their_own_frames():
    mav = FakeMav()
    with mavlink2keelson.subscribe(mav, types=("PARAM_VALUE",)) as sub_a:
        with mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",)) as sub_b:
            mav.fire(_msg("PARAM_VALUE", param_id="A"))
            mav.fire(_msg("COMMAND_ACK", command=1))
            mav.fire(_msg("PARAM_VALUE", param_id="B"))
    assert [
        m.param_id for m in (sub_a.queue.get_nowait(), sub_a.queue.get_nowait())
    ] == [
        "A",
        "B",
    ]
    assert sub_b.queue.qsize() == 1
    assert sub_b.queue.get_nowait().command == 1


# ---------------------------------------------------------------------------
# Robustness: queue overflow, predicate exceptions
# ---------------------------------------------------------------------------


def test_queue_overflow_drops_and_logs():
    """Excess frames must be dropped silently (warning-logged), never raise.

    A raise from a hook would propagate out of pymavlink's ``post_message``
    and kill the recv loop — that's the failure mode this catches."""
    mav = FakeMav()
    with mavlink2keelson.subscribe(mav, types=("HEARTBEAT",), maxsize=2) as sub:
        mav.fire(_msg("HEARTBEAT"))
        mav.fire(_msg("HEARTBEAT"))
        # These must not raise even though the queue is full.
        mav.fire(_msg("HEARTBEAT"))
        mav.fire(_msg("HEARTBEAT"))
        assert sub.queue.full()
        assert sub._dropped == 2


def test_predicate_exception_is_swallowed():
    """A buggy predicate must not propagate out of the hook into the
    recv loop."""
    mav = FakeMav()

    def boom(_m):
        raise ValueError("predicate is broken")

    with mavlink2keelson.subscribe(mav, types=("HEARTBEAT",), predicate=boom) as sub:
        # Firing must not raise even though the predicate does.
        mav.fire(_msg("HEARTBEAT"))
        mav.fire(_msg("HEARTBEAT"))
    assert sub.queue.qsize() == 0


# ---------------------------------------------------------------------------
# Blocking wait
# ---------------------------------------------------------------------------


def test_get_returns_frame_within_timeout():
    """A producer thread fires a matching frame; the consumer waiting on
    ``sub.get(timeout)`` must receive it. Exercises the threaded path
    that real RPC handlers will use."""
    mav = FakeMav()
    with mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",)) as sub:

        def producer():
            time.sleep(0.05)
            mav.fire(_msg("COMMAND_ACK", command=7))

        t = threading.Thread(target=producer)
        t.start()
        try:
            got = sub.get(timeout=1.0)
            assert got.command == 7
        finally:
            t.join()


def test_get_raises_queue_empty_on_timeout():
    mav = FakeMav()
    with mavlink2keelson.subscribe(mav, types=("COMMAND_ACK",)) as sub:
        with pytest.raises(queue.Empty):
            sub.get(timeout=0.05)


# ---------------------------------------------------------------------------
# Dispatch hook (registered by run() at startup)
# ---------------------------------------------------------------------------


def test_dispatch_hook_filters_bad_data_and_wrong_target():
    """The dispatch hook folds the BAD_DATA / target_system /
    target_component filters that used to live in the main loop. Verify
    each filter independently rejects without invoking dispatch()."""
    calls = []

    def fake_dispatch(msg, session, realm, entity_id, source_id):
        calls.append(msg.get_type())
        return 1

    orig = mavlink2keelson.dispatch
    mavlink2keelson.dispatch = fake_dispatch
    try:
        hook = mavlink2keelson._make_dispatch_hook(
            session=object(),
            realm="rise",
            entity_id="boat",
            source_id="ap",
            target_system=1,
            target_component=1,
        )
        mav = FakeMav()
        mav.message_hooks.append(hook)

        def msg_with_addr(t, sys_, comp):
            m = _msg(t)
            m.get_srcSystem = lambda: sys_
            m.get_srcComponent = lambda: comp
            return m

        mav.fire(msg_with_addr("BAD_DATA", 1, 1))  # rejected: BAD_DATA
        mav.fire(msg_with_addr("HEARTBEAT", 2, 1))  # rejected: wrong sysid
        mav.fire(msg_with_addr("HEARTBEAT", 1, 2))  # rejected: wrong compid
        mav.fire(msg_with_addr("HEARTBEAT", 0, 1))  # accepted: broadcast sysid
        mav.fire(msg_with_addr("HEARTBEAT", 1, 0))  # accepted: broadcast compid
        mav.fire(msg_with_addr("HEARTBEAT", 1, 1))  # accepted
    finally:
        mavlink2keelson.dispatch = orig

    assert calls == ["HEARTBEAT", "HEARTBEAT", "HEARTBEAT"]


def test_dispatch_hook_swallows_dispatch_exceptions():
    """A raising dispatch() must not propagate into pymavlink. The recv
    loop has to keep running even if a per-frame mapper is broken."""

    def boom(*_a, **_kw):
        raise RuntimeError("mapper exploded")

    orig = mavlink2keelson.dispatch
    mavlink2keelson.dispatch = boom
    try:
        hook = mavlink2keelson._make_dispatch_hook(
            session=object(),
            realm="rise",
            entity_id="boat",
            source_id="ap",
            target_system=1,
            target_component=1,
        )
        mav = FakeMav()
        mav.message_hooks.append(hook)

        def msg_with_addr(t):
            m = _msg(t)
            m.get_srcSystem = lambda: 1
            m.get_srcComponent = lambda: 1
            return m

        # Must not raise.
        mav.fire(msg_with_addr("HEARTBEAT"))
        mav.fire(msg_with_addr("ATTITUDE"))
    finally:
        mavlink2keelson.dispatch = orig
