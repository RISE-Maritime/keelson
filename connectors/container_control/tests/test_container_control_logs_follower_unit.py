"""Log following: line reassembly, level sniffing, rate capping, publishing."""

import queue
import time

import pytest
from keelson.payloads.foxglove.Log_pb2 import Log

from container_control.logs_follower import (
    SUBJECT,
    LineBuffer,
    LogFollower,
    RateLimiter,
    sniff_level,
)

pytestmark = pytest.mark.unit


class TestLineBuffer:
    def test_a_whole_line_comes_straight_out(self):
        assert LineBuffer().feed(b"one\n") == [b"one"]

    def test_a_line_split_across_frames_is_rejoined(self):
        # The follow generator yields frames, not lines. Emitting the halves
        # separately would cut messages at arbitrary points.
        buffer = LineBuffer()
        assert buffer.feed(b"hello wor") == []
        assert buffer.feed(b"ld\n") == [b"hello world"]

    def test_a_line_spanning_three_frames(self):
        buffer = LineBuffer()
        assert buffer.feed(b"a") == [] and buffer.feed(b"b") == []
        assert buffer.feed(b"c\n") == [b"abc"]

    def test_several_lines_in_one_frame(self):
        assert LineBuffer().feed(b"one\ntwo\nthree\n") == [b"one", b"two", b"three"]

    def test_the_tail_is_held_until_its_newline(self):
        buffer = LineBuffer()
        assert buffer.feed(b"done\npartial") == [b"done"]
        assert buffer.feed(b"-rest\n") == [b"partial-rest"]

    def test_flush_returns_an_unterminated_final_line(self):
        # A container killed mid-line still wrote that line.
        buffer = LineBuffer()
        buffer.feed(b"no trailing newline")
        assert buffer.flush() == [b"no trailing newline"]

    def test_flush_is_empty_when_nothing_is_pending(self):
        assert LineBuffer().flush() == []
        buffer = LineBuffer()
        buffer.feed(b"complete\n")
        assert buffer.flush() == []


class TestLevelSniffing:
    @pytest.mark.parametrize(
        ("text", "level"),
        [
            ("2026-01-01 ERROR something failed", Log.ERROR),
            ("WARN disk nearly full", Log.WARNING),
            ("WARNING disk nearly full", Log.WARNING),
            ("[debug] connecting", Log.DEBUG),
            ("FATAL: cannot continue", Log.FATAL),
            ("CRITICAL failure", Log.FATAL),
            ("panic: runtime error: index out of range", Log.FATAL),
            ("INFO ready", Log.INFO),
            ("plain message", Log.INFO),
        ],
    )
    def test_maps_a_standalone_token(self, text, level):
        assert sniff_level(text) == level

    @pytest.mark.parametrize(
        "text",
        [
            "no errors found",  # the word, not a level
            "terrorist attack averted",  # 'err' inside a word
            "the winfo module loaded",  # 'info' inside a word
            "unwarranted panic buying",  # 'warn' inside a word; bare 'panic' is English
        ],
    )
    def test_does_not_match_inside_a_word_or_mid_sentence(self, text):
        # Getting this wrong paints the Log panel red and teaches the operator
        # to ignore the colour.
        assert sniff_level(text) == Log.INFO

    def test_only_looks_near_the_start(self):
        assert sniff_level("ok " * 40 + "ERROR") == Log.INFO

    def test_case_insensitive(self):
        assert sniff_level("Error: nope") == Log.ERROR


class TestRateLimiter:
    def test_allows_up_to_the_budget(self):
        limiter = RateLimiter(3)
        assert [limiter.allow(100.0) for _ in range(3)] == [True, True, True]

    def test_sheds_the_excess(self):
        limiter = RateLimiter(2)
        assert [limiter.allow(100.0) for _ in range(4)] == [True, True, False, False]

    def test_the_drop_is_reported_not_silent(self):
        limiter = RateLimiter(1)
        for _ in range(4):
            limiter.allow(100.0)
        note = limiter.overflow_note()
        assert note is not None and "dropped 3" in note

    def test_the_note_clears_after_reading(self):
        limiter = RateLimiter(1)
        limiter.allow(100.0)
        limiter.allow(100.0)
        assert limiter.overflow_note() is not None
        assert limiter.overflow_note() is None

    def test_the_budget_refills_next_second(self):
        limiter = RateLimiter(1)
        assert limiter.allow(100.0) and not limiter.allow(100.5)
        assert limiter.allow(101.1)

    def test_zero_means_no_cap(self):
        limiter = RateLimiter(0)
        assert all(limiter.allow(100.0) for _ in range(1000))


class FakePublisher:
    def __init__(self):
        self.puts = []

    def put(self, payload):
        self.puts.append(payload)


def make_follower(**kwargs):
    follower = LogFollower(
        backend=None,
        session=None,
        base_path="rise",
        entity_id="testbed",
        source_id="host-1",
        globs=("*",),
        **kwargs,
    )
    return follower


class TestQueueing:
    def test_overflow_drops_the_oldest_and_keeps_going(self):
        # The RPC surface matters more than any single log line: enqueueing
        # must never block or raise, however hard a container is spraying.
        follower = make_follower(queue_size=2)
        for i in range(5):
            follower._enqueue("app", f"line-{i}", Log.INFO, 100.0 + i)
        drained = []
        while True:
            try:
                drained.append(follower._queue.get_nowait()[1])
            except queue.Empty:
                break
        assert drained == ["line-3", "line-4"]
        assert follower._dropped_by_queue == 3

    def test_the_subject_is_the_well_known_one(self):
        # Anything else records as an undecodable blob rather than foxglove.Log.
        assert SUBJECT == "log_message"


class TestPublishing:
    def test_publishes_a_foxglove_log_per_line(self, monkeypatch):
        import keelson

        follower = make_follower()
        published = FakePublisher()
        monkeypatch.setattr(follower, "_publisher_for", lambda _c: published)

        follower._enqueue("app", "ERROR it broke", Log.ERROR, 1767225600.0)
        follower._stop.clear()

        import threading

        thread = threading.Thread(target=follower._publish_loop, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while not published.puts and time.monotonic() < deadline:
            time.sleep(0.02)
        follower._stop.set()
        thread.join(timeout=3)

        assert published.puts, "nothing was published"
        _received, _enclosed, payload = keelson.uncover(published.puts[0])
        log = Log.FromString(payload)
        assert log.message == "ERROR it broke"
        assert log.level == Log.ERROR
        assert log.name == "app"  # which container, for the Foxglove panel
        assert log.timestamp.seconds == 1767225600

    def test_the_key_is_one_channel_per_container(self):
        # The recorder makes an MCAP channel per full key, so the container has
        # to be in the key -- otherwise every container shares one channel and
        # cannot be toggled independently in Foxglove.
        import keelson

        expected = keelson.construct_pubsub_key(
            base_path="rise",
            entity_id="testbed",
            subject=SUBJECT,
            source_id="host-1/app",
        )
        assert expected == "rise/@v0/testbed/pubsub/log_message/host-1/app"
