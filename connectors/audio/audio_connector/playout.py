"""Live playout: frames from any number of sources, mixed into one output.

Frames are pushed from the subscriber thread as they arrive. The output side,
a sound device callback or a file-writing clock, pulls fixed-size blocks with
:meth:`Mixer.read`. Each source has its own jitter buffer and decoder, and the
mix is a clipped sum, so a bridge mic and a VHF receiver can be monitored
together.

Frames are decoded at playout time, in sequence order, because Opus decoding
carries state from one packet to the next.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from keelson.payloads.AudioFrame_pb2 import AudioFrame

from .codec import FrameDecoder
from .jitter import JitterBuffer, Pop

logger = logging.getLogger(__name__)


@dataclass
class _Source:
    buffer: JitterBuffer
    decoder: FrameDecoder
    pending: np.ndarray
    last_rx: float
    nominal_us: int = 20000
    decode_errors: int = field(default=0)


class Mixer:
    def __init__(
        self,
        out_rate: int,
        out_channels: int,
        *,
        target_frames: int = 3,
        max_frames: int = 25,
        idle_timeout_s: float = 5.0,
        clock=time.monotonic,
    ):
        self.out_rate = out_rate
        self.out_channels = out_channels
        self.target_frames = target_frames
        self.max_frames = max_frames
        self.idle_timeout_s = idle_timeout_s
        self._clock = clock
        self._sources: dict[str, _Source] = {}
        self._lock = threading.Lock()

    @property
    def sources(self) -> list[str]:
        with self._lock:
            return list(self._sources)

    def stats(self, key: str):
        with self._lock:
            return self._sources[key].buffer.stats

    def push(self, key: str, frame: AudioFrame) -> None:
        with self._lock:
            src = self._sources.get(key)
            if src is None:
                logger.info("New audio source %s", key)
                src = _Source(
                    buffer=JitterBuffer(self.target_frames, self.max_frames),
                    decoder=FrameDecoder(self.out_rate, self.out_channels),
                    pending=np.zeros((0, self.out_channels), dtype=np.int16),
                    last_rx=self._clock(),
                )
                self._sources[key] = src
            src.last_rx = self._clock()
            if frame.duration_us:
                src.nominal_us = frame.duration_us
            src.buffer.push(frame.sequence, frame)

    def _fill(self, src: _Source, samples: int) -> np.ndarray:
        """Up to ``samples`` of this source's audio, zero-padded on underrun."""
        parts = [src.pending]
        have = len(src.pending)
        while have < samples:
            kind, frame = src.buffer.pop()
            if kind is Pop.EMPTY:
                break
            if kind is Pop.FRAME:
                try:
                    pcm = src.decoder.decode(frame)
                except Exception as exc:  # a corrupt or unsupported frame
                    src.decode_errors += 1
                    logger.debug("Dropping undecodable frame: %s", exc)
                    pcm = src.decoder.silence(frame.duration_us or src.nominal_us)
            else:  # Pop.GAP: the frame is missing; play its length in silence
                pcm = src.decoder.silence(src.nominal_us)
            parts.append(pcm)
            have += len(pcm)
        audio = np.concatenate(parts) if len(parts) > 1 else parts[0]
        src.pending = audio[samples:]
        out = audio[:samples]
        if len(out) < samples:
            out = np.concatenate(
                [out, np.zeros((samples - len(out), self.out_channels), dtype=np.int16)]
            )
        return out

    def read(self, samples: int) -> np.ndarray:
        """The next ``samples`` of mixed output, int16 ``(samples, channels)``."""
        mix = np.zeros((samples, self.out_channels), dtype=np.int32)
        now = self._clock()
        with self._lock:
            for key in list(self._sources):
                src = self._sources[key]
                if now - src.last_rx > self.idle_timeout_s and not len(src.buffer):
                    logger.info("Audio source %s went quiet", key)
                    del self._sources[key]
                    continue
                mix += self._fill(src, samples)
        return np.clip(mix, -32768, 32767).astype(np.int16)
