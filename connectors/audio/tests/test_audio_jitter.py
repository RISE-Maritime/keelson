"""Unit tests for audio_connector.jitter."""

import pytest

from audio_connector.jitter import JitterBuffer, Pop

pytestmark = pytest.mark.unit


def drain(buf, n):
    return [buf.pop() for _ in range(n)]


def test_prefills_before_playing():
    buf = JitterBuffer(target_frames=3, max_frames=10)
    buf.push(0, "a")
    buf.push(1, "b")
    assert buf.pop() == (Pop.EMPTY, None)
    buf.push(2, "c")
    assert drain(buf, 3) == [(Pop.FRAME, "a"), (Pop.FRAME, "b"), (Pop.FRAME, "c")]


def test_reorders():
    buf = JitterBuffer(target_frames=3)
    for seq in (12, 10, 11):
        buf.push(seq, seq)
    assert [item for _, item in drain(buf, 3)] == [10, 11, 12]


def test_gap_then_resume():
    buf = JitterBuffer(target_frames=2)
    for seq in (0, 1, 3, 4):
        buf.push(seq, seq)
    kinds = drain(buf, 5)
    assert kinds == [
        (Pop.FRAME, 0),
        (Pop.FRAME, 1),
        (Pop.GAP, None),
        (Pop.FRAME, 3),
        (Pop.FRAME, 4),
    ]
    assert buf.stats.gaps == 1


def test_late_frame_is_dropped():
    buf = JitterBuffer(target_frames=1)
    buf.push(5, 5)
    assert buf.pop() == (Pop.FRAME, 5)
    buf.push(4, 4)
    assert len(buf) == 0 and buf.stats.late == 1


def test_duplicate_is_ignored():
    buf = JitterBuffer(target_frames=2)
    buf.push(0, "first")
    buf.push(0, "again")
    assert buf.stats.duplicates == 1 and len(buf) == 1


def test_overflow_trims_to_target_keeping_newest():
    buf = JitterBuffer(target_frames=3, max_frames=5)
    for seq in range(6):
        buf.push(seq, seq)
    assert len(buf) == 3
    assert buf.stats.overflow_dropped == 3
    assert [item for _, item in drain(buf, 3)] == [3, 4, 5]


def test_overflow_while_playing_moves_playout_forward():
    buf = JitterBuffer(target_frames=1, max_frames=3)
    buf.push(0, 0)
    assert buf.pop() == (Pop.FRAME, 0)
    for seq in range(1, 5):
        buf.push(seq, seq)
    assert buf.pop() == (Pop.FRAME, 4)


def test_underrun_prefills_again():
    buf = JitterBuffer(target_frames=2)
    buf.push(0, 0)
    buf.push(1, 1)
    drain(buf, 2)
    assert buf.pop() == (Pop.EMPTY, None)
    assert buf.stats.underruns == 1 and not buf.playing
    buf.push(2, 2)
    assert buf.pop() == (Pop.EMPTY, None)  # prefilling
    buf.push(3, 3)
    assert drain(buf, 2) == [(Pop.FRAME, 2), (Pop.FRAME, 3)]


def test_restart_far_behind_resets():
    buf = JitterBuffer(target_frames=1, max_frames=5)
    buf.push(1000, "old")
    buf.pop()
    buf.push(0, "restarted")
    assert buf.stats.resets == 1
    assert buf.pop() == (Pop.FRAME, "restarted")


def test_jump_far_ahead_resets():
    buf = JitterBuffer(target_frames=1, max_frames=5)
    buf.push(0, 0)
    buf.pop()
    buf.push(500, 500)
    assert buf.stats.resets == 1
    assert buf.pop() == (Pop.FRAME, 500)


def test_rejects_bad_sizes():
    with pytest.raises(ValueError):
        JitterBuffer(target_frames=0)
    with pytest.raises(ValueError):
        JitterBuffer(target_frames=5, max_frames=4)
