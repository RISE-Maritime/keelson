"""The talk-back floor of one audio output (protocols/audio_floor.yaml).

At most one claimant holds the floor. The arbiter, the vessel-side sink, is
the only place that decides:

- **Grant.** A request is granted when the floor is idle, or when its priority
  is strictly higher than the holder's (the holder is preempted). A request
  from the holder itself keeps its grant: the time limit is not extended.
- **The stream is the heartbeat.** The holder's frames keep the floor. Once
  none have arrived for ``silence_timeout_s`` (counted from the grant until the
  first frame) the floor is released.
- **Hard limit.** ``max_transmit_s`` after the grant the floor is released
  whatever is happening, so a stuck transmitter cannot hold a channel.

No zenoh and no audio here: the sink feeds in requests, frames and clock ticks,
and acts on the changes this reports.
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from keelson.payloads.AudioOutputState_pb2 import AudioOutputState

Floor = AudioOutputState.Floor
Reason = AudioOutputState.ReleaseReason

WILDCARDS = ("*", "$*")


class Refusal(enum.Enum):
    INVALID = "invalid"  # malformed request
    BUSY = "busy"  # held by an equal or higher priority


class FloorRefused(Exception):
    def __init__(self, kind: Refusal, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Grant:
    holder_id: str
    source_key: str
    priority: int
    granted_at: float  # wall clock, s
    deadline: float  # monotonic, s


@dataclass(frozen=True)
class Change:
    """The floor moved. ``previous`` lost it (if any), ``current`` holds it (if any)."""

    previous: Optional[Grant]
    current: Optional[Grant]
    reason: Optional[int]  # Reason, for `previous`


def check_source_key(key: str) -> None:
    """A holder speaks on exactly one audio_frame key."""
    import keelson

    if not key:
        raise FloorRefused(Refusal.INVALID, "source_key is required")
    if any(w in key for w in WILDCARDS):
        raise FloorRefused(Refusal.INVALID, f"source_key {key!r} has a wildcard")
    try:
        subject = keelson.get_subject_from_pubsub_key(key)
    except ValueError:
        raise FloorRefused(
            Refusal.INVALID, f"source_key {key!r} is not a pubsub key"
        ) from None
    if subject != "audio_frame":
        raise FloorRefused(
            Refusal.INVALID, f"source_key {key!r} is not an audio_frame key"
        )


class FloorArbiter:
    def __init__(
        self,
        *,
        silence_timeout_s: float = 3.0,
        max_transmit_s: float = 120.0,
        on_change: Callable[[Change], None] = lambda change: None,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ):
        if silence_timeout_s <= 0 or max_transmit_s <= 0:
            raise ValueError("timeouts must be positive")
        self.silence_timeout_s = silence_timeout_s
        self.max_transmit_s = max_transmit_s
        self._on_change = on_change
        self._mono = monotonic
        self._wall = wall
        self._lock = threading.RLock()
        self._grant: Optional[Grant] = None
        self._last_activity = 0.0
        self.last_reason: int = Reason.RELEASE_REASON_UNSPECIFIED
        self.last_holder_id = ""

    @property
    def grant(self) -> Optional[Grant]:
        with self._lock:
            return self._grant

    def _change(self, new: Optional[Grant], reason: Optional[int]) -> Change:
        change = Change(self._grant, new, reason)
        if self._grant is not None and reason is not None:
            self.last_reason = reason
            self.last_holder_id = self._grant.holder_id
        self._grant = new
        if new is not None:
            self._last_activity = self._mono()
        return change

    def request(self, claimant_id: str, source_key: str, priority: int) -> Grant:
        """Grant the floor or raise :class:`FloorRefused`."""
        if not claimant_id:
            raise FloorRefused(Refusal.INVALID, "claimant_id is required")
        check_source_key(source_key)
        with self._lock:
            held = self._grant
            if held is not None and held.holder_id == claimant_id:
                if held.source_key == source_key and held.priority == priority:
                    return held
                # Same holder, new key or priority: same grant, same deadline.
                updated = replace(held, source_key=source_key, priority=priority)
                change = Change(held, updated, None)
                self._grant = updated
            elif held is not None and priority <= held.priority:
                raise FloorRefused(
                    Refusal.BUSY,
                    f"the floor is held by {held.holder_id} at priority {held.priority}",
                )
            else:
                now = self._mono()
                new = Grant(
                    claimant_id,
                    source_key,
                    priority,
                    self._wall(),
                    now + self.max_transmit_s,
                )
                change = self._change(
                    new, Reason.RELEASE_REASON_PREEMPTED if held else None
                )
            self._on_change(change)
            return self._grant

    def release(self, claimant_id: str) -> bool:
        """Release if ``claimant_id`` holds the floor. Idempotent."""
        with self._lock:
            if self._grant is None or self._grant.holder_id != claimant_id:
                return False
            change = self._change(None, Reason.RELEASE_REASON_RELEASED)
            self._on_change(change)
            return True

    def frame(self, key: str) -> None:
        """A frame arrived on ``key``; the holder's keep the floor."""
        with self._lock:
            if self._grant is not None and key == self._grant.source_key:
                self._last_activity = self._mono()

    def tick(self) -> None:
        """Apply the silence and the time limit. Call often (e.g. every 50 ms)."""
        with self._lock:
            if self._grant is None:
                return
            now = self._mono()
            if now >= self._grant.deadline:
                reason = Reason.RELEASE_REASON_MAX_DURATION
            elif now - self._last_activity >= self.silence_timeout_s:
                reason = Reason.RELEASE_REASON_SILENCE
            else:
                return
            change = self._change(None, reason)
            self._on_change(change)

    def shutdown(self) -> None:
        with self._lock:
            if self._grant is not None:
                change = self._change(None, Reason.RELEASE_REASON_SHUTDOWN)
                self._on_change(change)

    def deadline_wall(self) -> Optional[float]:
        """The current grant's hard limit on the wall clock."""
        with self._lock:
            if self._grant is None:
                return None
            return self._grant.granted_at + self.max_transmit_s
