"""Unit tests for keelson.scaffolding.track_store."""

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from keelson.scaffolding.track_store import TrackPoint, TrackStore


def _now_ns():
    return time.time_ns()


class TestTrackPoint:
    def test_creation_with_defaults(self):
        p = TrackPoint(timestamp_ns=1000, latitude=57.0, longitude=11.0)
        assert p.heading is None
        assert p.speed is None
        assert p.course is None

    def test_creation_with_all_fields(self):
        p = TrackPoint(1000, 57.0, 11.0, heading=180.0, speed=12.5, course=178.0)
        assert p.heading == 180.0
        assert p.speed == 12.5
        assert p.course == 178.0


class TestTrackStoreRecord:
    def test_record_and_get_single_target(self):
        store = TrackStore()
        ts = _now_ns()
        for i in range(5):
            store.record(123456789, ts + i * 1_000_000_000, 57.0 + i * 0.001, 11.0)

        tracks = store.get_tracks()
        assert 123456789 in tracks
        assert len(tracks[123456789]) == 5

    def test_record_and_get_multiple_targets(self):
        store = TrackStore()
        ts = _now_ns()
        for mmsi in [111, 222, 333]:
            store.record(mmsi, ts, 57.0, 11.0)

        tracks = store.get_tracks()
        assert len(tracks) == 3
        assert all(mmsi in tracks for mmsi in [111, 222, 333])

    def test_target_count(self):
        store = TrackStore()
        ts = _now_ns()
        store.record(111, ts, 57.0, 11.0)
        store.record(222, ts, 58.0, 12.0)
        assert store.target_count() == 2


class TestTrackStorePruning:
    def test_auto_prune_on_record(self):
        store = TrackStore(max_age_s=0.1)
        ts_old = _now_ns() - 200_000_000  # 200ms ago (older than 100ms window)
        ts_new = _now_ns()

        store.record(111, ts_old, 57.0, 11.0)
        store.record(111, ts_new, 57.1, 11.1)

        tracks = store.get_tracks()
        assert len(tracks[111]) == 1
        assert tracks[111][0]["lat"] == 57.1

    def test_max_points_per_target(self):
        store = TrackStore(max_points_per_target=5)
        ts = _now_ns()
        for i in range(10):
            store.record(111, ts + i, 57.0 + i * 0.001, 11.0)

        tracks = store.get_tracks()
        assert len(tracks[111]) == 5
        # Should keep the 5 most recent
        assert tracks[111][-1]["lat"] == pytest.approx(57.0 + 9 * 0.001)

    def test_prune_all_removes_stale_targets(self):
        store = TrackStore(max_age_s=0.1)
        ts_old = _now_ns() - 200_000_000  # 200ms ago

        store.record(111, ts_old, 57.0, 11.0)
        store.record(222, _now_ns(), 58.0, 12.0)

        store.prune_all()

        tracks = store.get_tracks()
        assert 111 not in tracks
        assert 222 in tracks
        assert store.target_count() == 1


class TestTrackStoreFiltering:
    def test_get_tracks_with_mmsi_filter(self):
        store = TrackStore()
        ts = _now_ns()
        store.record(111, ts, 57.0, 11.0)
        store.record(222, ts, 58.0, 12.0)
        store.record(333, ts, 59.0, 13.0)

        tracks = store.get_tracks(target_id=222)
        assert len(tracks) == 1
        assert 222 in tracks

    def test_get_tracks_with_since_filter(self):
        store = TrackStore()
        ts = _now_ns()
        store.record(111, ts - 2_000_000_000, 57.0, 11.0)
        store.record(111, ts - 1_000_000_000, 57.1, 11.1)
        store.record(111, ts, 57.2, 11.2)

        tracks = store.get_tracks(since_ns=ts - 1_500_000_000)
        assert len(tracks[111]) == 2

    def test_get_tracks_nonexistent_target(self):
        store = TrackStore()
        tracks = store.get_tracks(target_id=999)
        assert tracks == {}


class TestTrackStoreJSON:
    def test_to_json_valid(self):
        store = TrackStore(max_age_s=60.0)
        ts = _now_ns()
        store.record(111, ts, 57.0, 11.0, heading=180.0, speed=12.5, course=178.0)

        result = json.loads(store.to_json())
        assert result["target_count"] == 1
        assert result["point_count"] == 1
        assert result["window_seconds"] == 60.0
        assert "111" in result["targets"]

        point = result["targets"]["111"][0]
        assert point["t"] == ts
        assert point["lat"] == 57.0
        assert point["lon"] == 11.0
        assert point["hdg"] == 180.0
        assert point["sog"] == 12.5
        assert point["cog"] == 178.0

    def test_none_optional_fields_omitted(self):
        store = TrackStore()
        ts = _now_ns()
        store.record(111, ts, 57.0, 11.0)

        result = json.loads(store.to_json())
        point = result["targets"]["111"][0]
        assert "hdg" not in point
        assert "sog" not in point
        assert "cog" not in point

    def test_to_json_with_filters(self):
        store = TrackStore()
        ts = _now_ns()
        store.record(111, ts, 57.0, 11.0)
        store.record(222, ts, 58.0, 12.0)

        result = json.loads(store.to_json(target_id=111))
        assert result["target_count"] == 1
        assert "111" in result["targets"]
        assert "222" not in result["targets"]


class TestTrackStoreThreadSafety:
    def test_concurrent_read_write(self):
        store = TrackStore()
        errors = []

        def writer():
            for i in range(500):
                try:
                    store.record(i % 10, _now_ns(), 57.0 + i * 0.0001, 11.0)
                except Exception as e:
                    errors.append(e)

        def reader():
            for _ in range(500):
                try:
                    store.get_tracks()
                    store.to_json()
                    store.target_count()
                except Exception as e:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(writer) for _ in range(2)]
            futures += [pool.submit(reader) for _ in range(2)]
            for f in as_completed(futures):
                f.result()

        assert errors == []
