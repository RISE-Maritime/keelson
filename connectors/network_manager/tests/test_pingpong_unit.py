"""Timing maths for the ping/pong exchange.

Times are in nanoseconds; helpers below build the four timestamps from a
scenario so each test reads as the situation it describes rather than as
arithmetic.
"""

import pytest

from network_manager.pingpong import compute

MS = 1_000_000  # ns


def exchange(*, one_way_ms, responder_delay_ms=0.0, skew_ms=0.0, t1=1_000_000_000):
    """Four timestamps for a symmetric link, with an optionally offset responder clock."""
    t4 = t1 + int(2 * one_way_ms * MS) + int(responder_delay_ms * MS)
    # The responder's clock runs `skew_ms` ahead of the pinger's.
    t2 = t1 + int(one_way_ms * MS) + int(skew_ms * MS)
    t3 = t2 + int(responder_delay_ms * MS)
    return t1, t2, t3, t4


class TestBasicTiming:
    def test_symmetric_link(self):
        r = compute(*exchange(one_way_ms=10))
        assert r.round_trip_time_ms == pytest.approx(20, abs=0.01)
        assert r.latency_ms == pytest.approx(10, abs=0.01)
        assert r.clock_skew_ms == pytest.approx(0, abs=0.01)

    def test_responder_delay_is_excluded_from_rtt(self):
        """A slow responder is not a slow link — the whole point of subtracting (t3-t2)."""
        fast = compute(*exchange(one_way_ms=10, responder_delay_ms=0))
        slow = compute(*exchange(one_way_ms=10, responder_delay_ms=200))
        assert slow.round_trip_time_ms == pytest.approx(fast.round_trip_time_ms, abs=0.01)
        assert slow.round_trip_time_ms == pytest.approx(20, abs=0.01)

    def test_zero_latency_localhost(self):
        r = compute(*exchange(one_way_ms=0))
        assert r.round_trip_time_ms == pytest.approx(0, abs=0.01)
        assert r.clock_skew_ms == pytest.approx(0, abs=0.01)


class TestClockSkew:
    """The case the RTT must survive: a responder whose clock is simply wrong."""

    @pytest.mark.parametrize("skew_ms", [-5000, -250, -1, 1, 250, 5000])
    def test_rtt_is_unaffected_by_any_skew(self, skew_ms):
        r = compute(*exchange(one_way_ms=10, skew_ms=skew_ms))
        assert r.round_trip_time_ms == pytest.approx(20, abs=0.01)

    @pytest.mark.parametrize("skew_ms", [-5000, -250, 250, 5000])
    def test_skew_is_recovered(self, skew_ms):
        r = compute(*exchange(one_way_ms=10, skew_ms=skew_ms))
        assert r.clock_skew_ms == pytest.approx(skew_ms, abs=0.01)

    def test_skew_and_responder_delay_together(self):
        r = compute(*exchange(one_way_ms=10, responder_delay_ms=100, skew_ms=1234))
        assert r.round_trip_time_ms == pytest.approx(20, abs=0.01)
        assert r.clock_skew_ms == pytest.approx(1234, abs=0.01)


class TestDegenerateInputs:
    def test_negative_rtt_is_clamped_not_published(self):
        """Responder claims to have spent longer than the whole round trip."""
        t1 = 0
        t4 = t1 + 10 * MS
        t2 = t1 + 1 * MS
        t3 = t2 + 50 * MS  # impossible
        r = compute(t1, t2, t3, t4)
        assert r.round_trip_time_ms == 0.0
        assert r.latency_ms == 0.0

    def test_latency_is_always_half_the_rtt(self):
        r = compute(*exchange(one_way_ms=7.5))
        assert r.latency_ms == pytest.approx(r.round_trip_time_ms / 2)


class TestPayloadSize:
    def test_bytes_convert_to_mb(self):
        r = compute(*exchange(one_way_ms=1), payload_size_bytes=1024 * 1024)
        assert r.payload_size_mb == pytest.approx(1.0)

    def test_default_is_zero(self):
        assert compute(*exchange(one_way_ms=1)).payload_size_mb == 0.0
