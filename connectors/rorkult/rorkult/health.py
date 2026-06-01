"""MCU-link health state + EntityHealth payload builder.

``HealthState`` is the single source of truth for "what's the MCU link
doing right now" — link up/down plus a small set of transport-level
metrics the supervisor updates on every connect attempt and every
byte received. The periodic publisher snapshots the state atomically
and ``build_entity_health`` lays it out as a typed protobuf payload.
``build_entity_health`` is a pure function so it stays testable
without touching Zenoh.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from keelson.payloads.EntityHealth_pb2 import (
    EntityHealth,
    HealthLevel,
)


@dataclass(frozen=True)
class Snapshot:
    """Atomic point-in-time view of HealthState.

    Returned by ``HealthState.snapshot``; all fields are sampled under
    the same lock acquisition so they're internally consistent.
    """

    level: int  # HealthLevel
    detail: str
    connect_attempts_since_success: int
    bytes_received_total: int
    last_byte_received_ns: Optional[int]  # None if never


class HealthState:
    """Thread-safe MCU-link state + transport metrics.

    Three logical states (never_connected / connected / disconnected)
    collapsed to ``HEALTH_NOMINAL`` when connected and
    ``HEALTH_CRITICAL`` otherwise. Metric counters (connect attempts,
    bytes received) reset semantics:

    - ``connect_attempts_since_success`` increments on every connect
      attempt, resets to 0 on every successful connect.
    - ``bytes_received_total`` is a monotonic gauge over the connector
      process lifetime (does NOT reset on reconnect — the operator
      cares about cumulative wire activity).
    """

    _NEVER = "never"
    _CONNECTED = "connected"
    _DISCONNECTED = "disconnected"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = self._NEVER
        self._detail = "no successful connect yet"
        self._connect_attempts_since_success = 0
        self._bytes_received_total = 0
        self._last_byte_received_ns: Optional[int] = None

    def mark_connect_attempt(self) -> None:
        with self._lock:
            self._connect_attempts_since_success += 1

    def mark_connected(self, endpoint: str) -> None:
        with self._lock:
            self._state = self._CONNECTED
            self._detail = f"connected to {endpoint}"
            self._connect_attempts_since_success = 0

    def mark_disconnected(self, reason: str) -> None:
        with self._lock:
            self._state = self._DISCONNECTED
            self._detail = reason

    def mark_bytes_received(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._bytes_received_total += n
            self._last_byte_received_ns = time.time_ns()

    def snapshot(self) -> Snapshot:
        with self._lock:
            level = (
                HealthLevel.HEALTH_NOMINAL
                if self._state == self._CONNECTED
                else HealthLevel.HEALTH_CRITICAL
            )
            return Snapshot(
                level=level,
                detail=self._detail,
                connect_attempts_since_success=(self._connect_attempts_since_success),
                bytes_received_total=self._bytes_received_total,
                last_byte_received_ns=self._last_byte_received_ns,
            )


def _age_detail(now_ns: int, when_ns: Optional[int]) -> str:
    if when_ns is None:
        return "never"
    age_s = max(0.0, (now_ns - when_ns) / 1e9)
    return f"{age_s:.2f}s ago"


def build_entity_health(
    state: HealthState, *, publish_rate_hz: float, timestamp_ns: Optional[int] = None
) -> EntityHealth:
    """Build an EntityHealth payload from the current HealthState.

    Top-level ``EntityHealth.level`` mirrors the MCU-link level (the
    MCU link is the only thing rorkult monitors today). One
    ``SourceHealth`` named ``"mcu_link"`` carries one ``SubjectHealth``
    named ``"tcp_connection"`` with multiple ``CheckResult`` entries:

    - ``"connected"``: HEALTH_NOMINAL / HEALTH_CRITICAL with the link
      detail string (last transition reason).
    - ``"connect_attempts_since_success"``: HEALTH_NOMINAL,
      informational counter ("0" while stable, climbing on retry).
    - ``"bytes_received_total"``: HEALTH_NOMINAL, monotonic cumulative
      bytes received from the MCU since startup.
    - ``"last_byte_received"``: HEALTH_NOMINAL, human-readable "Xs
      ago" / "never". Stays NOMINAL today because we don't have a
      framing-derived expected cadence yet; once framing lands this
      check gains a real freshness gate.

    All metrics emit at HEALTH_NOMINAL today: they're observability
    surfaces, not health verdicts. The overall link-up/-down signal is
    carried by ``"connected"``.
    """
    snap = state.snapshot()
    ts_ns = timestamp_ns if timestamp_ns is not None else time.time_ns()

    msg = EntityHealth()
    msg.timestamp.FromNanoseconds(ts_ns)
    msg.level = snap.level
    msg.rate_hz = publish_rate_hz

    src = msg.sources.add()
    src.name = "mcu_link"
    src.level = snap.level

    sub = src.subjects.add()
    sub.name = "tcp_connection"
    sub.level = snap.level
    sub.measured_publication_rate_hz = 0.0  # framing-gated

    def _add(name: str, level: int, detail: str) -> None:
        c = sub.checks.add()
        c.name = name
        c.level = level
        c.detail = detail

    _add("connected", snap.level, snap.detail)
    _add(
        "connect_attempts_since_success",
        HealthLevel.HEALTH_NOMINAL,
        str(snap.connect_attempts_since_success),
    )
    _add(
        "bytes_received_total",
        HealthLevel.HEALTH_NOMINAL,
        str(snap.bytes_received_total),
    )
    _add(
        "last_byte_received",
        HealthLevel.HEALTH_NOMINAL,
        _age_detail(ts_ns, snap.last_byte_received_ns),
    )

    return msg
