"""Track history store for AIS targets and other tracked entities.

Provides an in-memory, time-windowed store of position track points
and a Zenoh queryable helper to expose the data via RPC.
"""

import json
import time
import logging
import threading
from collections import deque
from dataclasses import dataclass

import zenoh

from keelson import construct_rpc_key
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TrackPoint:
    timestamp_ns: int
    latitude: float
    longitude: float
    heading: float | None = None
    speed: float | None = None
    course: float | None = None


class TrackStore:
    """Thread-safe, time-windowed store of position track points per target ID.

    Args:
        max_age_s: Maximum age of track points in seconds (default 30 minutes).
        max_points_per_target: Hard cap on points per target (prevents unbounded
            growth even if the time window is large).
    """

    def __init__(
        self, max_age_s: float = 1800.0, max_points_per_target: int = 10000
    ):
        self._max_age_ns: int = int(max_age_s * 1_000_000_000)
        self._max_points: int = max_points_per_target
        self._tracks: dict[int, deque[TrackPoint]] = {}
        self._lock = threading.Lock()
        self._record_count = 0

    @property
    def max_age_s(self) -> float:
        return self._max_age_ns / 1_000_000_000

    def record(
        self,
        target_id: int,
        timestamp_ns: int,
        lat: float,
        lon: float,
        heading: float | None = None,
        speed: float | None = None,
        course: float | None = None,
    ) -> None:
        """Add a track point and prune stale entries for this target."""
        point = TrackPoint(timestamp_ns, lat, lon, heading, speed, course)
        with self._lock:
            if target_id not in self._tracks:
                self._tracks[target_id] = deque(maxlen=self._max_points)
            self._tracks[target_id].append(point)
            self._prune_target(target_id)
            self._record_count += 1
            if self._record_count % 1000 == 0:
                self._prune_all_locked()

    def get_tracks(
        self,
        target_id: int | None = None,
        since_ns: int | None = None,
    ) -> dict[int, list[dict]]:
        """Return track history as {target_id: [point_dicts]}.

        Args:
            target_id: If given, only return data for this target.
            since_ns: If given, only return points newer than this timestamp.
        """
        with self._lock:
            if target_id is not None:
                targets = {target_id: self._tracks.get(target_id, deque())}
            else:
                targets = self._tracks

            result = {}
            for tid, points in targets.items():
                filtered = [
                    _point_to_dict(p)
                    for p in points
                    if since_ns is None or p.timestamp_ns > since_ns
                ]
                if filtered:
                    result[tid] = filtered
            return result

    def to_json(
        self,
        target_id: int | None = None,
        since_ns: int | None = None,
    ) -> str:
        """Serialize track data as a JSON string."""
        tracks = self.get_tracks(target_id=target_id, since_ns=since_ns)
        point_count = sum(len(pts) for pts in tracks.values())
        return json.dumps(
            {
                "targets": {str(tid): pts for tid, pts in tracks.items()},
                "target_count": len(tracks),
                "point_count": point_count,
                "window_seconds": self.max_age_s,
            }
        )

    def prune_all(self) -> None:
        """Remove stale entries across all targets and drop empty targets."""
        with self._lock:
            self._prune_all_locked()

    def target_count(self) -> int:
        """Return number of currently tracked targets."""
        with self._lock:
            return len(self._tracks)

    # --- internal helpers (must be called with lock held) ---

    def _prune_target(self, target_id: int) -> None:
        cutoff = time.time_ns() - self._max_age_ns
        dq = self._tracks.get(target_id)
        if dq is None:
            return
        while dq and dq[0].timestamp_ns < cutoff:
            dq.popleft()

    def _prune_all_locked(self) -> None:
        cutoff = time.time_ns() - self._max_age_ns
        empty = []
        for tid, dq in self._tracks.items():
            while dq and dq[0].timestamp_ns < cutoff:
                dq.popleft()
            if not dq:
                empty.append(tid)
        for tid in empty:
            del self._tracks[tid]


def _point_to_dict(p: TrackPoint) -> dict:
    d: dict = {"t": p.timestamp_ns, "lat": p.latitude, "lon": p.longitude}
    if p.heading is not None:
        d["hdg"] = p.heading
    if p.speed is not None:
        d["sog"] = p.speed
    if p.course is not None:
        d["cog"] = p.course
    return d


def make_track_queryable(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    responder_id: str,
    store: TrackStore,
):
    """Declare a Zenoh queryable that serves track history from a TrackStore.

    Query parameters (semicolon-separated in Zenoh selector):
        mmsi: (int) Filter to a single target MMSI.
        since: (int) Nanosecond timestamp — only return points newer than this.

    The queryable is declared on:
        {base_path}/@v0/{entity_id}/@rpc/get_track_history/{responder_id}
    """
    key = construct_rpc_key(base_path, entity_id, "get_track_history", responder_id)

    def _handle_query(query: zenoh.Query):
        try:
            logger.debug("Track history query on: %s", query.key_expr)

            target_id = None
            since_ns = None

            params = str(query.parameters) if query.parameters else ""
            if params:
                for part in params.split(";"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        if k == "mmsi":
                            target_id = int(v)
                        elif k == "since":
                            since_ns = int(v)

            payload = store.to_json(target_id=target_id, since_ns=since_ns)
            query.reply(key, payload.encode())

        except Exception as exc:
            logger.exception("Failed to handle track history query")
            query.reply_err(
                ErrorResponse(error_description=str(exc)).SerializeToString()
            )

    session.declare_queryable(key, _handle_query, complete=True)
    logger.info("Track history queryable declared on: %s", key)
