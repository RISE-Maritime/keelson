"""MCU-link health state + EntityHealth payload builder.

``HealthState`` is the single source of truth for "is rorkult talking
to its MCU"; the supervisor mutates it, the periodic publisher reads
it. ``build_entity_health`` is a pure function so it stays testable
without touching Zenoh.
"""

from __future__ import annotations

import threading
import time
from typing import Tuple

from keelson.payloads.EntityHealth_pb2 import (
    EntityHealth,
    HealthLevel,
)


class HealthState:
    """Thread-safe MCU-link state.

    Three logical states (never_connected / connected / disconnected),
    collapsed to ``HEALTH_NOMINAL`` when connected and
    ``HEALTH_CRITICAL`` otherwise. The detail string preserves the
    last transition's context for the EntityHealth payload.
    """

    _NEVER = "never"
    _CONNECTED = "connected"
    _DISCONNECTED = "disconnected"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = self._NEVER
        self._detail = "no successful connect yet"

    def mark_connected(self, endpoint: str) -> None:
        with self._lock:
            self._state = self._CONNECTED
            self._detail = f"connected to {endpoint}"

    def mark_disconnected(self, reason: str) -> None:
        with self._lock:
            self._state = self._DISCONNECTED
            self._detail = reason

    def snapshot(self) -> Tuple[int, str]:
        """Atomic read of (level, detail)."""
        with self._lock:
            if self._state == self._CONNECTED:
                return HealthLevel.HEALTH_NOMINAL, self._detail
            return HealthLevel.HEALTH_CRITICAL, self._detail


def build_entity_health(
    state: HealthState, *, publish_rate_hz: float, timestamp_ns: int | None = None
) -> EntityHealth:
    """Build an EntityHealth payload from the current HealthState.

    The wire layout follows the convention used by the entity_health
    connector: top-level EntityHealth.level == overall (= the MCU link
    level here, since the MCU link is the only thing rorkult monitors
    today), and a single SourceHealth named ``"mcu_link"`` carrying
    one SubjectHealth named ``"tcp_connection"`` with a CheckResult
    that owns the human-readable detail string. Once framing lands
    and we monitor MCU-reported subjects, those become additional
    SubjectHealth entries under the same SourceHealth.
    """
    level, detail = state.snapshot()
    ts_ns = timestamp_ns if timestamp_ns is not None else time.time_ns()

    msg = EntityHealth()
    msg.timestamp.FromNanoseconds(ts_ns)
    msg.level = level
    msg.rate_hz = publish_rate_hz

    src = msg.sources.add()
    src.name = "mcu_link"
    src.level = level

    sub = src.subjects.add()
    sub.name = "tcp_connection"
    sub.level = level
    sub.measured_publication_rate_hz = 0.0  # no MCU-fed subject yet

    chk = sub.checks.add()
    chk.name = "connected"
    chk.level = level
    chk.detail = detail

    return msg
