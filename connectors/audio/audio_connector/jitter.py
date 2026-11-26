"""Sequence-ordered playout buffer for one live audio source.

Frames arrive best-effort: late, out of order, duplicated, or not at all. The
buffer holds them by ``sequence`` and hands them out in order, one per playout
tick:

- **Prefill.** Playout starts only once ``target_frames`` are buffered, which
  absorbs network jitter at the cost of ``target_frames`` frames of latency.
  After an underrun (nothing left to play) it prefills again.
- **Gaps.** A missing sequence with later frames already buffered is reported
  as ``GAP`` so the caller plays silence for it and moves on.
- **Late frames.** A frame whose slot has already been played is dropped.
- **Overflow.** Past ``max_frames`` the oldest frames are dropped down to
  ``target_frames``. This bounds latency when the producer outruns playout,
  e.g. a recording replayed faster than real time.
- **Restarts.** A sequence far behind or far ahead of the playout point means
  the source restarted (or a long outage); the buffer resets onto it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Pop(enum.Enum):
    FRAME = "frame"  # the next frame, in order
    GAP = "gap"  # the next frame is missing; later ones are buffered
    EMPTY = "empty"  # nothing to play (prefilling or underrun)


@dataclass
class JitterStats:
    played: int = 0
    gaps: int = 0
    late: int = 0
    duplicates: int = 0
    overflow_dropped: int = 0
    resets: int = 0
    underruns: int = 0


@dataclass
class JitterBuffer:
    target_frames: int = 3
    max_frames: int = 25
    stats: JitterStats = field(default_factory=JitterStats)

    def __post_init__(self):
        if self.target_frames < 1:
            raise ValueError("target_frames must be at least 1")
        if self.max_frames < self.target_frames:
            raise ValueError("max_frames must be at least target_frames")
        self._frames: dict[int, Any] = {}
        self._next: int | None = None  # next sequence to play, once playing
        self._playing = False

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def playing(self) -> bool:
        return self._playing

    def reset(self) -> None:
        self._frames.clear()
        self._next = None
        self._playing = False

    def push(self, sequence: int, item: Any) -> None:
        if self._next is not None:
            behind = self._next - sequence
            if behind > 0:
                if behind > self.max_frames:
                    # Far behind what we have played: the source restarted.
                    self.stats.resets += 1
                    self.reset()
                else:
                    self.stats.late += 1
                    return
            elif -behind > 2 * self.max_frames:
                # Far ahead: a long outage or a restart at a higher sequence.
                self.stats.resets += 1
                self.reset()

        if sequence in self._frames:
            self.stats.duplicates += 1
            return
        self._frames[sequence] = item

        if len(self._frames) > self.max_frames:
            for seq in sorted(self._frames)[: len(self._frames) - self.target_frames]:
                del self._frames[seq]
                self.stats.overflow_dropped += 1
            if self._next is not None:
                self._next = min(self._frames)

    def pop(self) -> tuple[Pop, Any]:
        if not self._playing:
            if len(self._frames) < self.target_frames:
                return Pop.EMPTY, None
            self._playing = True
            self._next = min(self._frames)

        if self._next in self._frames:
            item = self._frames.pop(self._next)
            self._next += 1
            self.stats.played += 1
            return Pop.FRAME, item

        if self._frames:
            self._next += 1
            self.stats.gaps += 1
            return Pop.GAP, None

        # Underrun: keep _next so a late arrival of it is still in order, but
        # prefill again before resuming.
        self._playing = False
        self.stats.underruns += 1
        return Pop.EMPTY, None
