"""keelson2mcap's log_time is arrival time, and its timestamp sanity checks (#168).

Unit tests: no Zenoh. A stub sample stands in for zenoh.Sample.
"""

import logging
import sys
import time
from importlib import import_module
from pathlib import Path
from queue import Queue

import pytest
from mcap.reader import make_reader

import keelson

bin_dir = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

keelson2mcap = import_module("keelson2mcap")
MCAPRotatingWriter = keelson2mcap.MCAPRotatingWriter
_TimestampMonitor = keelson2mcap._TimestampMonitor
_enqueue_with_arrival = keelson2mcap._enqueue_with_arrival
_process_sample = keelson2mcap._process_sample

KEY = "rise/@v0/boat/pubsub/heading_true_north_deg/gnss"
S = 1_000_000_000


class _Payload:
    def __init__(self, data: bytes):
        self._data = data

    def to_bytes(self) -> bytes:
        return self._data


class _Sample:
    def __init__(self, key: str, data: bytes):
        self.key_expr = key
        self.payload = _Payload(data)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _record(tmp_path, items, monitor=None):
    writer = MCAPRotatingWriter(output_folder=tmp_path, file_pattern="ts_%f")
    writer.open()
    try:
        for arrival_ns, sample in items:
            _process_sample(writer, arrival_ns, sample, monitor)
        path = writer._current_path
    finally:
        writer.close()
    with path.open("rb") as fh:
        return [
            (m.log_time, m.publish_time) for _, _, m in make_reader(fh).iter_messages()
        ]


# -----------------------------------------------------------------------------
# log_time is arrival
# -----------------------------------------------------------------------------


@pytest.mark.unit
def test_log_time_is_arrival_not_when_the_recorder_got_to_it(tmp_path):
    """The regression #168 describes: a sample that waited 10 s in the queue is recorded at
    the time it arrived, not the time the recorder thread processed it."""
    enclosed_at = time.time_ns() - 10 * S
    arrival_ns = enclosed_at + 5_000_000  # arrived 5 ms after it was enclosed, 10 s ago
    sample = _Sample(KEY, keelson.enclose(b"x", enclosed_at=enclosed_at))

    [(log_time, publish_time)] = _record(tmp_path, [(arrival_ns, sample)])
    assert log_time == arrival_ns
    assert publish_time == enclosed_at


@pytest.mark.unit
def test_first_and_later_samples_on_a_key_both_use_arrival(tmp_path):
    """The first sample registers the channel and later ones take the known-key path; both
    must write the arrival time."""
    base = time.time_ns() - 30 * S
    items = [
        (base + i * S, _Sample(KEY, keelson.enclose(b"x", enclosed_at=base + i * S)))
        for i in range(3)
    ]
    recorded = _record(tmp_path, items)
    assert [lt for lt, _ in recorded] == [base, base + S, base + 2 * S]


@pytest.mark.unit
def test_enqueue_stamps_arrival_at_callback_time():
    q = Queue()
    before = time.time_ns()
    _enqueue_with_arrival(q)("sample")
    after = time.time_ns()
    arrival_ns, sample = q.get_nowait()
    assert sample == "sample"
    assert before <= arrival_ns <= after


# -----------------------------------------------------------------------------
# _TimestampMonitor
# -----------------------------------------------------------------------------


def _monitor(skew_s=5.0, lag_s=1.0, clock=None):
    return _TimestampMonitor(
        max_skew_s=skew_s,
        max_lag_s=lag_s,
        warn_interval_s=60.0,
        clock=clock or _Clock(),
    )


@pytest.mark.unit
@pytest.mark.parametrize("offset_s", [-6.0, 6.0])
def test_skew_beyond_threshold_warns_either_way(caplog, offset_s):
    m = _monitor()
    arrival = 1_000 * S
    with caplog.at_level(logging.WARNING, logger="mcap-record"):
        m.observe(KEY, arrival, arrival - int(offset_s * S), arrival)
    assert m.skewed[KEY] == 1
    assert KEY in caplog.text and "--max-clock-skew-s" in caplog.text
    assert ("behind" if offset_s > 0 else "ahead of") in caplog.text


@pytest.mark.unit
def test_skew_within_threshold_is_quiet(caplog):
    m = _monitor()
    with caplog.at_level(logging.WARNING, logger="mcap-record"):
        m.observe(KEY, 1_000 * S, 1_000 * S - 4 * S, 1_000 * S)
    assert not m.skewed and not caplog.records


@pytest.mark.unit
def test_zero_thresholds_disable_both_checks(caplog):
    m = _monitor(skew_s=0, lag_s=0)
    with caplog.at_level(logging.WARNING, logger="mcap-record"):
        m.observe(KEY, 1_000 * S, 1 * S, 2_000 * S)
        m.observe(KEY, 1_000 * S, 0, 2_000 * S)
    assert not caplog.records
    assert m.summary() == []


@pytest.mark.unit
def test_unset_enclosed_at_is_its_own_warning(caplog):
    m = _monitor()
    with caplog.at_level(logging.WARNING, logger="mcap-record"):
        m.observe(KEY, 1_000 * S, 0, 1_000 * S)
    assert m.unset[KEY] == 1 and not m.skewed
    assert "no enclosed_at" in caplog.text


@pytest.mark.unit
def test_warnings_are_rate_limited_per_key_but_still_counted(caplog):
    clock = _Clock()
    m = _monitor(clock=clock)
    other = KEY.replace("gnss", "gnss2")
    with caplog.at_level(logging.WARNING, logger="mcap-record"):
        for _ in range(5):
            m.observe(KEY, 1_000 * S, 900 * S, 1_000 * S)
        m.observe(other, 1_000 * S, 900 * S, 1_000 * S)  # another key warns on its own
        assert len(caplog.records) == 2
        clock.t = 61.0
        m.observe(KEY, 1_000 * S, 900 * S, 1_000 * S)
        assert len(caplog.records) == 3
    assert m.skewed[KEY] == 6


@pytest.mark.unit
def test_queue_lag_warns_with_the_lag_and_queue_size(caplog):
    m = _monitor()
    with caplog.at_level(logging.WARNING, logger="mcap-record"):
        m.observe(KEY, 1_000 * S, 1_000 * S, 1_000 * S + 3 * S, queue_size=742)
    assert m.lagged == 1 and m.worst_lag_ns == 3 * S
    assert "3.000 s behind" in caplog.text and "742" in caplog.text


@pytest.mark.unit
def test_summary_names_the_worst_keys(tmp_path):
    m = _monitor()
    a, b = KEY, KEY.replace("gnss", "ais")
    m.observe(a, 1_000 * S, 990 * S, 1_000 * S)  # 10 s behind
    m.observe(b, 1_000 * S, 1_100 * S, 1_000 * S)  # 100 s ahead
    m.observe(b, 1_000 * S, 0, 1_000 * S)
    lines = m.summary()
    assert lines[0].startswith("2 key(s)")
    assert lines[0].index(b) < lines[0].index(a)  # worst first
    assert "-100.000 s" in lines[0] and "+10.000 s" in lines[0]
    assert "no enclosed_at" in lines[1]


@pytest.mark.unit
def test_monitor_never_changes_what_is_written(tmp_path):
    """A wildly skewed sample is still recorded, with its own timestamps."""
    enclosed_at = 1 * S  # 1970
    arrival_ns = time.time_ns()
    sample = _Sample(KEY, keelson.enclose(b"x", enclosed_at=enclosed_at))
    [(log_time, publish_time)] = _record(
        tmp_path, [(arrival_ns, sample)], monitor=_monitor()
    )
    assert (log_time, publish_time) == (arrival_ns, enclosed_at)
