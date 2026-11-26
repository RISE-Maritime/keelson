"""Unit tests for audio_connector.floor (the talk-back arbiter)."""

import pytest

from audio_connector.floor import FloorArbiter, FloorRefused, Reason, Refusal

pytestmark = pytest.mark.unit

A_KEY = "rise/@v0/roc/pubsub/audio_frame/talkback/a"
B_KEY = "rise/@v0/roc/pubsub/audio_frame/talkback/b"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def changes():
    return []


@pytest.fixture
def arbiter(clock, changes):
    return FloorArbiter(
        silence_timeout_s=3.0,
        max_transmit_s=60.0,
        on_change=changes.append,
        monotonic=clock,
        wall=lambda: 1_000.0,
    )


def test_grant_when_idle(arbiter, changes):
    grant = arbiter.request("a", A_KEY, 0)
    assert grant.holder_id == "a" and grant.source_key == A_KEY
    assert len(changes) == 1
    assert changes[0].previous is None and changes[0].current == grant
    assert arbiter.deadline_wall() == 1_060.0


def test_equal_priority_is_refused(arbiter):
    arbiter.request("a", A_KEY, 5)
    with pytest.raises(FloorRefused) as exc:
        arbiter.request("b", B_KEY, 5)
    assert exc.value.kind is Refusal.BUSY
    assert "held by a" in str(exc.value)


def test_higher_priority_preempts(arbiter, changes):
    arbiter.request("a", A_KEY, 1)
    grant = arbiter.request("b", B_KEY, 2)
    assert grant.holder_id == "b"
    assert changes[-1].previous.holder_id == "a"
    assert changes[-1].reason == Reason.RELEASE_REASON_PREEMPTED
    assert arbiter.last_reason == Reason.RELEASE_REASON_PREEMPTED
    assert arbiter.last_holder_id == "a"


def test_holder_rerequest_keeps_the_deadline(arbiter, clock, changes):
    first = arbiter.request("a", A_KEY, 0)
    clock.t += 30
    again = arbiter.request("a", A_KEY, 0)
    assert again == first and len(changes) == 1
    moved = arbiter.request("a", B_KEY, 3)
    assert moved.deadline == first.deadline and moved.source_key == B_KEY
    assert changes[-1].previous.source_key == A_KEY


def test_release_by_holder_and_idempotent_otherwise(arbiter, changes):
    arbiter.request("a", A_KEY, 0)
    assert not arbiter.release("b")
    assert arbiter.release("a")
    assert arbiter.grant is None
    assert changes[-1].reason == Reason.RELEASE_REASON_RELEASED
    assert not arbiter.release("a")


def test_frames_from_the_holder_keep_the_floor(arbiter, clock):
    arbiter.request("a", A_KEY, 0)
    for _ in range(10):
        clock.t += 1.0
        arbiter.frame(A_KEY)
        arbiter.tick()
    assert arbiter.grant is not None


def test_silence_releases(arbiter, clock, changes):
    arbiter.request("a", A_KEY, 0)
    clock.t += 1.0
    arbiter.frame(A_KEY)
    clock.t += 2.9
    arbiter.tick()
    assert arbiter.grant is not None
    arbiter.frame(B_KEY)  # someone else's frames do not count
    clock.t += 0.2
    arbiter.tick()
    assert arbiter.grant is None
    assert changes[-1].reason == Reason.RELEASE_REASON_SILENCE


def test_silence_counts_from_the_grant(arbiter, clock):
    arbiter.request("a", A_KEY, 0)
    clock.t += 3.0
    arbiter.tick()
    assert arbiter.grant is None


def test_time_limit_ends_an_active_grant(arbiter, clock, changes):
    arbiter.request("a", A_KEY, 0)
    while clock.t < 160.0:
        clock.t += 1.0
        arbiter.frame(A_KEY)
        arbiter.tick()
    assert arbiter.grant is None
    assert changes[-1].reason == Reason.RELEASE_REASON_MAX_DURATION


def test_shutdown_releases(arbiter, changes):
    arbiter.request("a", A_KEY, 0)
    arbiter.shutdown()
    assert changes[-1].reason == Reason.RELEASE_REASON_SHUTDOWN
    arbiter.shutdown()  # nothing held: no change
    assert len(changes) == 2


@pytest.mark.parametrize(
    "claimant, key",
    [
        ("", A_KEY),
        ("a", ""),
        ("a", "rise/@v0/roc/pubsub/audio_frame/talkback/*"),
        ("a", "rise/@v0/roc/pubsub/location_fix/gnss"),
        ("a", "not a key"),
    ],
)
def test_invalid_requests(arbiter, claimant, key):
    with pytest.raises(FloorRefused) as exc:
        arbiter.request(claimant, key, 0)
    assert exc.value.kind is Refusal.INVALID
    assert arbiter.grant is None
