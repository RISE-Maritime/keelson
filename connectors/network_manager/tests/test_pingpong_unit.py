"""Timing maths for the ping/pong exchange.

Times are in nanoseconds; helpers below build the four timestamps from a
scenario so each test reads as the situation it describes rather than as
arithmetic.
"""

import pytest

from network_manager.pingpong import PeerWindow, ResponderWindow, compute

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


class TestPeerWindow:
    """Loss over a window, per peer — and published every round (#314)."""

    def _window(self, rounds, window_s=60.0):
        w = PeerWindow(window_s=window_s)
        for at, answered in rounds:
            w.record(at, answered)
        return w

    def test_no_rounds_has_no_loss_rather_than_zero(self):
        assert PeerWindow().loss_pct() is None

    def test_loss_is_the_share_of_unanswered_rounds(self):
        w = self._window([(0, True), (10, False), (20, True), (30, True)])
        assert w.loss_pct() == pytest.approx(25.0)

    def test_a_total_outage_is_100_percent_not_silence(self):
        """The case that matters most: nothing answered, and it says so."""
        w = self._window([(0, False), (10, False)])
        assert w.loss_pct() == pytest.approx(100.0)

    def test_rounds_older_than_the_window_drop_out(self):
        w = self._window([(0, False), (5, False)], window_s=30)
        w.record(40, True)
        assert len(w) == 1
        assert w.loss_pct() == pytest.approx(0.0)

    def test_loss_can_age_without_a_new_round(self):
        w = self._window([(0, False), (10, True)], window_s=30)
        assert w.loss_pct(end_s=35) == pytest.approx(0.0)

    def test_span_is_what_is_covered_not_the_configured_window(self):
        """Ten seconds after startup the window covers ten seconds, not 60."""
        w = PeerWindow(window_s=60)
        w.record(100, True, end_s=102)
        assert w.span_s(102) == pytest.approx(2.0)
        w.record(110, True, end_s=112)
        assert w.span_s(112) == pytest.approx(12.0)

    def test_span_never_exceeds_the_window_once_full(self):
        w = PeerWindow(window_s=30)
        for at in range(0, 100, 10):
            w.record(at, True, end_s=at + 2)
        assert w.span_s(92) <= 30


class TestResponderWindow:
    """Jitter and p95 per responder: one link, never a mix of several."""

    def _window(self, rtts, window_s=60.0):
        w = ResponderWindow(window_s=window_s)
        for i, rtt in enumerate(rtts):
            w.record(i * 10, rtt)
        return w

    def test_one_answer_has_neither_figure_rather_than_zero(self):
        """A single sample has no variation; 0 would claim a perfectly steady link."""
        w = self._window([20.0])
        assert w.jitter_ms() is None
        assert w.p95_ms() is None

    def test_jitter_is_the_mean_change_between_answers(self):
        assert self._window([20.0, 30.0, 10.0]).jitter_ms() == pytest.approx(15.0)

    def test_a_steady_link_has_zero_jitter(self):
        assert self._window([20.0, 20.0, 20.0]).jitter_ms() == pytest.approx(0.0)

    def test_p95_is_by_nearest_rank(self):
        # 20 answers 1..20 ms: rank ceil(0.95 * 20) = 19, so 19 ms.
        w = self._window([float(v) for v in range(20, 0, -1)], window_s=1000)
        assert w.p95_ms() == pytest.approx(19.0)

    def test_p95_of_two_is_the_larger(self):
        assert self._window([10.0, 40.0]).p95_ms() == pytest.approx(40.0)

    def test_p95_is_an_rtt_that_was_measured(self):
        rtts = [12.0, 15.0, 900.0, 14.0, 13.0]
        assert self._window(rtts).p95_ms() in rtts

    def test_alternating_fastest_responder_makes_no_fake_jitter(self):
        """Review point 2: timing every round by whichever responder answered
        first compares two links whenever the fastest changes. Per responder,
        two steady links stay steady."""
        near, far = ResponderWindow(), ResponderWindow()
        for i in range(6):
            near.record(i * 10, 5.0)
            far.record(i * 10, 80.0)
        fastest_first = [5.0, 80.0, 5.0, 80.0]  # what statuses[0] would have seen
        assert self._window(fastest_first).jitter_ms() == pytest.approx(75.0)
        assert near.jitter_ms() == pytest.approx(0.0)
        assert far.jitter_ms() == pytest.approx(0.0)

    def test_answers_older_than_the_window_drop_out(self):
        w = self._window([900.0, 20.0], window_s=30)
        w.age(45)
        assert len(w) == 0
