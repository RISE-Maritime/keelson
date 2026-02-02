"""Liveliness utilities for Keelson health monitoring."""

import logging
import threading

import zenoh

from keelson import construct_liveliness_key

logger = logging.getLogger(__name__)


def declare_liveliness_token(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
):
    """Declare a liveliness token for a source.

    The returned token must be stored by the caller for as long as the source
    is alive. Dropping or undeclaring the token signals a leave event to
    liveliness subscribers.

    Args:
        session: Active Zenoh session.
        base_path: Base path for Keelson keys.
        entity_id: Entity identifier.
        source_id: Source identifier (e.g. ``gnss/0``).

    Returns:
        A Zenoh liveliness token handle.
    """
    key = construct_liveliness_key(base_path, entity_id, source_id)
    return session.liveliness().declare_token(key)


class LivelinessMonitor:
    """Monitor liveliness tokens and track alive sources.

    Wraps a Zenoh liveliness subscriber to maintain a thread-safe set of
    currently alive key expressions. Optional callbacks are fired on join
    and leave events.

    Args:
        session: Active Zenoh session.
        key_expr: Key expression pattern to monitor (e.g. ``keelson/@v0/**``).
        on_join: Optional callback ``(key_expr: str) -> None`` fired on join.
        on_leave: Optional callback ``(key_expr: str) -> None`` fired on leave.
        history: If True (default), query existing tokens on startup.
    """

    def __init__(
        self,
        session: zenoh.Session,
        key_expr: str,
        on_join=None,
        on_leave=None,
        history=True,
    ):
        self._session = session
        self._key_expr = key_expr
        self._on_join = on_join
        self._on_leave = on_leave
        self._alive: set[str] = set()
        self._lock = threading.Lock()

        self._subscriber = session.liveliness().declare_subscriber(
            key_expr, self._on_event, history=history
        )

    def _on_event(self, sample):
        key = str(sample.key_expr)
        kind = sample.kind

        if kind == zenoh.SampleKind.PUT:
            with self._lock:
                self._alive.add(key)
            if self._on_join is not None:
                try:
                    self._on_join(key)
                except Exception:
                    logger.exception("on_join callback error for %s", key)

        elif kind == zenoh.SampleKind.DELETE:
            with self._lock:
                self._alive.discard(key)
            if self._on_leave is not None:
                try:
                    self._on_leave(key)
                except Exception:
                    logger.exception("on_leave callback error for %s", key)

    def get_alive(self) -> set:
        """Return a copy of the currently alive key expressions."""
        with self._lock:
            return set(self._alive)

    def is_alive(self, key_expr: str) -> bool:
        """Check whether a specific key expression is currently alive."""
        with self._lock:
            return key_expr in self._alive

    def count(self) -> int:
        """Return the number of currently alive sources."""
        with self._lock:
            return len(self._alive)

    def close(self):
        """Undeclare the liveliness subscriber."""
        if self._subscriber is not None:
            self._subscriber.undeclare()
            self._subscriber = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
