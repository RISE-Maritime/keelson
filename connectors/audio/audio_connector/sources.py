"""Capture sources: a sound device, or any URL or file PyAV can open.

Both produce fixed-size int16 blocks of shape ``(frame_samples, channels)``,
each with the wall-clock time of its first sample, through an ``on_block``
callback. The caller owns threading and queuing.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable
from urllib.parse import urlparse

import av
import numpy as np

from .codec import layout_for

logger = logging.getLogger(__name__)

OnBlock = Callable[[int, np.ndarray], None]

LIVE_SCHEMES = {"rtsp", "rtsps", "rtmp", "rtmps", "srt", "udp", "rtp", "http", "https"}

# A live source's own clock may drift from ours, and it may stall. When the
# sample-count timeline drifts this far from wall-clock, re-anchor it.
REANCHOR_NS = 250_000_000


def is_live_url(url: str) -> bool:
    return urlparse(url).scheme.lower() in LIVE_SCHEMES


class Timeline:
    """Capture time of each block, from a sample counter anchored to the wall clock.

    A sample counter gives evenly spaced timestamps even when a source delivers
    in bursts; anchoring keeps them honest against the wall clock.
    """

    def __init__(
        self,
        sample_rate: int,
        clock: Callable[[], int] = time.time_ns,
        *,
        reanchor: bool = True,
    ):
        self.sample_rate = sample_rate
        self._clock = clock
        # Off for a file read faster than real time: its sample count is the
        # truth, and the wall clock says nothing about when it was captured.
        self._reanchor = reanchor
        self._anchor_ns: int | None = None
        self._samples = 0

    def stamp(self, block_samples: int, now_ns: int | None = None) -> int:
        """Timestamp of the block about to be emitted, then advance."""
        now_ns = self._clock() if now_ns is None else now_ns
        block_ns = block_samples * 1_000_000_000 // self.sample_rate
        if self._anchor_ns is None:
            self._anchor_ns, self._samples = now_ns - block_ns, 0
        ts = self._anchor_ns + self._samples * 1_000_000_000 // self.sample_rate
        if self._reanchor and abs((now_ns - block_ns) - ts) > REANCHOR_NS:
            logger.debug(
                "Re-anchoring capture timeline (drift %d ms)",
                (now_ns - block_ns - ts) // 1_000_000,
            )
            self._anchor_ns, self._samples = now_ns - block_ns, 0
            ts = self._anchor_ns
        self._samples += block_samples
        return ts

    def start_at(self, start_ns: int) -> None:
        self._anchor_ns, self._samples = start_ns, 0

    def advance(self, block_samples: int) -> int:
        """Paced mode: timestamp of the next block, with no wall-clock check."""
        ts = self.due_ns()
        self._samples += block_samples
        return ts

    def due_ns(self) -> int:
        """Wall-clock time at which every block so far has fully elapsed."""
        return self._anchor_ns + self._samples * 1_000_000_000 // self.sample_rate


def capture_url(
    url: str,
    *,
    sample_rate: int,
    channels: int,
    frame_samples: int,
    on_block: OnBlock,
    stop: threading.Event,
    pace: bool | None = None,
) -> None:
    """Decode the first audio stream of ``url`` and emit blocks until it ends.

    ``pace`` holds a file to real time so its audio is published as fast as it
    plays; by default files are paced and network streams are not.
    """
    if pace is None:
        pace = not is_live_url(url)
    options = {"rtsp_transport": "tcp"} if url.lower().startswith("rtsp") else {}
    container = av.open(url, options=options, timeout=10.0)
    try:
        if not container.streams.audio:
            raise ValueError(f"{url} has no audio stream")
        stream = container.streams.audio[0]
        logger.info(
            "Audio from %s: %s, %d Hz, %d channel(s)",
            url,
            stream.codec_context.name,
            stream.codec_context.sample_rate,
            stream.codec_context.layout.nb_channels,
        )
        resampler = av.AudioResampler(
            format="s16", layout=layout_for(channels), rate=sample_rate
        )
        fifo = av.AudioFifo()
        timeline = Timeline(sample_rate, reanchor=is_live_url(url))
        if pace:
            timeline.start_at(time.time_ns())

        def drain(final: bool = False) -> None:
            while fifo.samples >= frame_samples or (final and fifo.samples):
                frame = (
                    fifo.read(frame_samples) if fifo.samples >= frame_samples else None
                )
                if frame is None:  # final partial block: pad with silence
                    rest = fifo.read()
                    block = np.zeros((frame_samples, channels), dtype=np.int16)
                    tail = rest.to_ndarray().reshape(-1, channels)
                    block[: len(tail)] = tail
                else:
                    block = frame.to_ndarray().reshape(-1, channels).astype(np.int16)
                if pace:
                    # Publish a block once its audio has elapsed, as a live
                    # capture would.
                    ts = timeline.advance(frame_samples)
                    delay = (timeline.due_ns() - time.time_ns()) / 1e9
                    if delay > 0 and stop.wait(delay):
                        return
                else:
                    ts = timeline.stamp(frame_samples)
                on_block(ts, block)
                if stop.is_set():
                    return

        for frame in container.decode(stream):
            if stop.is_set():
                return
            frame.pts = None
            for out in resampler.resample(frame):
                out.pts = None
                fifo.write(out)
            drain()
        for out in resampler.resample(None):
            out.pts = None
            fifo.write(out)
        drain(final=True)
        logger.info("End of %s", url)
    finally:
        container.close()


class DeviceCapture:
    """Capture from a PortAudio input device, one block per callback.

    ``sounddevice`` is imported here, not at module load, so the rest of the
    connector (``--help``, URL capture, tests) works without libportaudio.
    """

    def __init__(
        self,
        device: str | int | None,
        *,
        sample_rate: int,
        channels: int,
        frame_samples: int,
        on_block: OnBlock,
    ):
        import sounddevice as sd

        self._on_block = on_block
        self._frame_ns = frame_samples * 1_000_000_000 // sample_rate
        self.overflows = 0
        self._stream = sd.InputStream(
            device=device,
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            blocksize=frame_samples,
            latency="low",
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        # Arrival time minus one block approximates the first sample's time.
        ts = time.time_ns() - self._frame_ns
        if status.input_overflow:
            self.overflows += 1
        self._on_block(ts, indata.copy())

    def __enter__(self) -> "DeviceCapture":
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()


def parse_device(value: str | None) -> str | int | None:
    """``--device`` accepts an index, a (partial) name, or "default"."""
    if value is None or value == "default":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def list_devices() -> str:
    import sounddevice as sd

    return str(sd.query_devices())
