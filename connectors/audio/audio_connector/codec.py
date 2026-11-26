"""PCM blocks <-> ``keelson.AudioFrame``.

PCM inside the connector is always a numpy ``int16`` array of shape
``(samples, channels)``. On the wire a frame is either one Opus packet (no
container) or the same int16 samples, interleaved little-endian.
"""

from __future__ import annotations

import fractions
import struct
from collections import deque

import av
import numpy as np

from keelson.payloads.AudioFrame_pb2 import AudioFrame

OPUS = AudioFrame.OPUS
PCM_S16LE = AudioFrame.PCM_S16LE

FORMATS = {"opus": OPUS, "pcm_s16le": PCM_S16LE}

# What libopus accepts. Opus in Ogg always counts time at 48 kHz.
OPUS_SAMPLE_RATES = (8000, 12000, 16000, 24000, 48000)
OPUS_FRAME_MS = (10, 20, 40, 60)
OPUS_MAX_CHANNELS = 2  # mapping family 0; more channels need a channel map


def layout_for(channels: int) -> av.AudioLayout:
    # PyAV takes layout names only; FFmpeg's "Nc" names a default layout of N
    # channels (2.1, 4.0, 5.1, ...) beyond mono and stereo.
    return av.AudioLayout({1: "mono", 2: "stereo"}.get(channels, f"{channels}c"))


def validate(fmt: int, sample_rate: int, channels: int, frame_ms: int) -> None:
    """Raise ValueError for a combination the encoder cannot produce."""
    if channels < 1:
        raise ValueError("channels must be at least 1")
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    if frame_ms <= 0 or (sample_rate * frame_ms) % 1000:
        raise ValueError(
            f"{frame_ms} ms is not a whole number of samples at {sample_rate} Hz"
        )
    if fmt == OPUS:
        if sample_rate not in OPUS_SAMPLE_RATES:
            raise ValueError(f"Opus sample rate must be one of {OPUS_SAMPLE_RATES}")
        if frame_ms not in OPUS_FRAME_MS:
            raise ValueError(f"Opus frame length must be one of {OPUS_FRAME_MS} ms")
        if channels > OPUS_MAX_CHANNELS:
            raise ValueError(f"Opus supports at most {OPUS_MAX_CHANNELS} channels here")


def _to_av_frame(pcm: np.ndarray, sample_rate: int) -> av.AudioFrame:
    channels = pcm.shape[1]
    frame = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(pcm).reshape(1, -1),
        format="s16",
        layout=layout_for(channels),
    )
    frame.sample_rate = sample_rate
    return frame


def _from_av_frames(frames, channels: int) -> np.ndarray:
    parts = [f.to_ndarray().reshape(-1, channels) for f in frames if f.samples]
    if not parts:
        return np.zeros((0, channels), dtype=np.int16)
    return np.concatenate(parts).astype(np.int16, copy=False)


class FrameEncoder:
    """Turns fixed-size PCM blocks into ``AudioFrame`` messages for one source.

    Every block passed to :meth:`encode` must be exactly ``frame_samples`` long.
    Sequence numbers start at 0 and increase by one per frame.
    """

    def __init__(
        self,
        fmt: int,
        sample_rate: int,
        channels: int,
        frame_ms: int,
        *,
        bitrate: int = 32000,
        frame_id: str = "",
    ):
        validate(fmt, sample_rate, channels, frame_ms)
        self.format = fmt
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms
        self.frame_samples = sample_rate * frame_ms // 1000
        self.duration_us = frame_ms * 1000
        self.frame_id = frame_id
        self._sequence = 0
        self._pts = 0
        # Capture time of each block handed to the encoder whose packet has not
        # come out yet. libopus emits one packet per block, in order.
        self._pending: deque[int] = deque()
        self._last_ns: int | None = None
        self._ctx = None
        if fmt == OPUS:
            ctx = av.CodecContext.create("libopus", "w")
            ctx.sample_rate = sample_rate
            ctx.layout = layout_for(channels)
            ctx.format = "s16"
            ctx.bit_rate = bitrate
            ctx.time_base = fractions.Fraction(1, sample_rate)
            ctx.options = {"frame_duration": str(frame_ms), "application": "voip"}
            ctx.open()
            self._ctx = ctx

    def _frame(self, data: bytes, capture_ns: int) -> AudioFrame:
        msg = AudioFrame(
            frame_id=self.frame_id,
            format=self.format,
            sample_rate=self.sample_rate,
            channels=self.channels,
            sequence=self._sequence,
            duration_us=self.duration_us,
            data=data,
        )
        msg.timestamp.FromNanoseconds(capture_ns)
        self._sequence += 1
        self._last_ns = capture_ns
        return msg

    def encode(self, pcm: np.ndarray, capture_ns: int) -> list[AudioFrame]:
        """Encode one block captured at ``capture_ns`` (its first sample)."""
        if pcm.dtype != np.int16 or pcm.ndim != 2:
            raise ValueError("expected int16 PCM of shape (samples, channels)")
        if pcm.shape != (self.frame_samples, self.channels):
            raise ValueError(
                f"expected a block of shape {(self.frame_samples, self.channels)}, "
                f"got {pcm.shape}"
            )
        if self.format == PCM_S16LE:
            data = np.ascontiguousarray(pcm).astype("<i2", copy=False).tobytes()
            return [self._frame(data, capture_ns)]

        frame = _to_av_frame(pcm, self.sample_rate)
        frame.pts = self._pts
        frame.time_base = self._ctx.time_base
        self._pts += self.frame_samples
        self._pending.append(capture_ns)
        return [
            self._frame(bytes(p), self._pending.popleft())
            for p in self._ctx.encode(frame)
        ]

    def flush(self) -> list[AudioFrame]:
        """Drain the encoder at end of stream."""
        if self._ctx is None:
            return []
        out = []
        for packet in self._ctx.encode(None):
            if self._pending:
                ts = self._pending.popleft()
            elif self._last_ns is not None:
                ts = self._last_ns + self.duration_us * 1000
            else:
                ts = 0
            out.append(self._frame(bytes(packet), ts))
        return out


class FrameDecoder:
    """Decodes one source's frames to int16 PCM at a fixed output rate and layout.

    The source may change format, rate or channel count mid-stream (a restart
    with new settings); the decoder and resampler are rebuilt when it does.
    """

    def __init__(self, out_rate: int, out_channels: int):
        self.out_rate = out_rate
        self.out_channels = out_channels
        self._params: tuple[int, int, int] | None = None
        self._decoder = None
        self._resampler = None

    def _configure(self, fmt: int, rate: int, channels: int) -> None:
        if self._params == (fmt, rate, channels):
            return
        self._params = (fmt, rate, channels)
        self._decoder = None
        if fmt == OPUS:
            dec = av.CodecContext.create("libopus", "r")
            dec.sample_rate = rate
            dec.layout = layout_for(channels)
            self._decoder = dec
        if rate == self.out_rate and channels == self.out_channels:
            self._resampler = None
        else:
            self._resampler = av.AudioResampler(
                format="s16", layout=layout_for(self.out_channels), rate=self.out_rate
            )

    def output_samples(self, duration_us: int) -> int:
        return round(duration_us * self.out_rate / 1_000_000)

    def silence(self, duration_us: int) -> np.ndarray:
        return np.zeros(
            (self.output_samples(duration_us), self.out_channels), dtype=np.int16
        )

    def decode(self, msg: AudioFrame) -> np.ndarray:
        """Decode one frame. Raises ValueError for an unknown format."""
        if msg.format not in (OPUS, PCM_S16LE):
            raise ValueError(f"unsupported audio format {msg.format}")
        if msg.channels < 1 or msg.sample_rate <= 0:
            raise ValueError("frame carries no channel count or sample rate")
        self._configure(msg.format, msg.sample_rate, msg.channels)

        if msg.format == OPUS:
            frames = self._decoder.decode(av.Packet(msg.data))
        else:
            pcm = np.frombuffer(msg.data, dtype="<i2").astype(np.int16)
            frames = [_to_av_frame(pcm.reshape(-1, msg.channels), msg.sample_rate)]

        if self._resampler is None:
            # Opus decodes to packed s16 already; PCM is already in shape.
            return _from_av_frames(frames, self.out_channels)
        out = []
        for frame in frames:
            frame.pts = None
            out.extend(self._resampler.resample(frame))
        return _from_av_frames(out, self.out_channels)


def opus_head(channels: int, input_rate: int, pre_skip: int = 0) -> bytes:
    """The 19-byte OpusHead (RFC 7845 §5.1) for mapping family 0.

    ``pre_skip`` is 0 by default: a recording segment usually starts mid-stream,
    where there is no encoder lookahead to skip, only audio.
    """
    return b"OpusHead" + struct.pack("<BBHIhB", 1, channels, pre_skip, input_rate, 0, 0)
