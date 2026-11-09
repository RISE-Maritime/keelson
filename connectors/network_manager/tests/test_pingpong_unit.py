"""Timing maths for the ping/pong exchange.

Times are in nanoseconds; helpers below build the four timestamps from a
scenario so each test reads as the situation it describes rather than as
arithmetic.
"""

import pytest

from network_manager.pingpong import LinkWindow, compute

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
        assert slow.round_trip_time_ms == pytest.approx(
            fast.round_trip_time_ms, abs=0.01
        )
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


class TestLinkWindow:
    """Jitter and loss over a window (#314) — what a single exchange cannot say."""

    def _window(self, rounds, window_s=60.0):
        w = LinkWindow(window_s=window_s)
        for at, rtt in rounds:
            w.record(at, rtt)
        return w

    def test_empty_window_has_no_jitter_and_no_counts(self):
        s = LinkWindow().stats()
        assert s.jitter_ms is None
        assert (s.pings_sent, s.pongs_received) == (0, 0)
        assert s.loss_ratio is None

    def test_one_answer_has_no_jitter_rather_than_zero(self):
        """A single sample has no variation; 0 would claim a perfectly steady link."""
        s = self._window([(0, 20.0)]).stats()
        assert s.jitter_ms is None
        assert (s.pings_sent, s.pongs_received) == (1, 1)

    def test_jitter_is_the_mean_change_between_answers(self):
        s = self._window([(0, 20.0), (10, 30.0), (20, 10.0)]).stats()
        assert s.jitter_ms == pytest.approx((10 + 20) / 2)

    def test_a_steady_link_has_zero_jitter(self):
        s = self._window([(0, 20.0), (10, 20.0), (20, 20.0)]).stats()
        assert s.jitter_ms == pytest.approx(0.0)

    def test_a_lost_round_counts_as_loss_not_as_a_jump(self):
        """Loss is already counted; making it jitter too would double-count it."""
        s = self._window([(0, 20.0), (10, None), (20, 20.0)]).stats()
        assert s.jitter_ms == pytest.approx(0.0)
        assert (s.pings_sent, s.pongs_received) == (3, 2)
        assert s.loss_ratio == pytest.approx(1 / 3)

    def test_all_lost_has_counts_but_no_jitter(self):
        s = self._window([(0, None), (10, None)]).stats()
        assert s.jitter_ms is None
        assert (s.pings_sent, s.pongs_received) == (2, 0)
        assert s.loss_ratio == pytest.approx(1.0)

    def test_rounds_older_than_the_window_drop_out(self):
        w = self._window([(0, 900.0), (5, None)], window_s=30)
        w.record(40, 20.0)
        s = w.stats()
        assert (s.pings_sent, s.pongs_received) == (1, 1)
        assert s.window_s == 30

    def test_stats_can_age_the_window_without_a_new_round(self):
        w = self._window([(0, 20.0), (10, 25.0)], window_s=30)
        assert w.stats(now_s=35).pings_sent == 1
