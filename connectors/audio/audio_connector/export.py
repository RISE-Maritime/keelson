"""MCAP recordings -> audio files, one or more per recorded source.

Every MCAP channel whose schema is ``keelson.AudioFrame`` is one source. Its
frames are put back in capture order (they travel best-effort, so a few may
have been recorded out of order) and written as:

- **Ogg Opus**, for Opus frames, by remuxing the packets as recorded: no
  re-encoding, so the file holds exactly what was on the bus.
- **WAV**, for PCM frames, or for Opus when ``--format wav`` asks for it.

The timeline is kept honest so the audio lines up with the rest of the
recording: a short hole (lost frames, a brief outage) is filled with silence,
and a long one, or a change of format, rate or channel count, ends the file
and starts a new one named after its own start time.
"""

from __future__ import annotations

import datetime
import fractions
import heapq
import logging
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import av
import numpy as np
from mcap.reader import make_reader

from keelson.payloads.AudioFrame_pb2 import AudioFrame

from .codec import OPUS, PCM_S16LE, FrameDecoder, layout_for, opus_head

logger = logging.getLogger(__name__)

SCHEMA_NAME = "keelson.AudioFrame"
REORDER_WINDOW = 50  # frames held back to restore capture order (1 s at 20 ms)
OGG_RATE = 48000  # Ogg Opus granule positions always count 48 kHz samples


@dataclass(frozen=True)
class Params:
    format: int
    sample_rate: int
    channels: int


def params_of(frame: AudioFrame) -> Params:
    return Params(frame.format, frame.sample_rate, frame.channels)


def source_slug(topic: str) -> str:
    """``rise/@v0/vessel/pubsub/audio_frame/audio/bridge`` -> ``vessel_audio-bridge``."""
    marker = "/pubsub/audio_frame/"
    if marker in topic:
        head, source = topic.split(marker, 1)
        entity = head.rsplit("/", 1)[-1]
        name = f"{entity}_{source.replace('/', '-')}"
    else:
        name = topic
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "audio"


def ns_to_stamp(ns: int) -> str:
    dt = datetime.datetime.fromtimestamp(ns / 1e9, tz=datetime.timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%S.") + f"{dt.microsecond // 1000:03d}Z"


def ns_of(frame: AudioFrame) -> int:
    return frame.timestamp.ToNanoseconds()


def in_capture_order(
    frames: Iterable[AudioFrame], window: int = REORDER_WINDOW
) -> Iterator[AudioFrame]:
    """Re-sort a nearly-sorted stream with a bounded window."""
    heap: list[tuple[int, int, AudioFrame]] = []
    for i, frame in enumerate(frames):
        heapq.heappush(heap, (ns_of(frame), i, frame))
        if len(heap) > window:
            yield heapq.heappop(heap)[2]
    while heap:
        yield heapq.heappop(heap)[2]


class _Segment:
    """One output file. Subclasses write frames and silence."""

    suffix = ""

    def __init__(self, path: Path, params: Params):
        self.path = path
        self.params = params

    def write(self, frame: AudioFrame) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def silence(self, duration_us: int) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class WavSegment(_Segment):
    suffix = ".wav"

    def __init__(self, path: Path, params: Params):
        super().__init__(path, params)
        self._decoder = FrameDecoder(params.sample_rate, params.channels)
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(params.channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(params.sample_rate)

    def write(self, frame: AudioFrame) -> None:
        self._wav.writeframes(self._decoder.decode(frame).astype("<i2").tobytes())

    def silence(self, duration_us: int) -> None:
        self._wav.writeframes(self._decoder.silence(duration_us).tobytes())

    def close(self) -> None:
        self._wav.close()


class OggOpusSegment(_Segment):
    suffix = ".ogg"

    def __init__(self, path: Path, params: Params):
        super().__init__(path, params)
        self._out = av.open(str(path), "w", format="ogg")
        self._stream = self._out.add_stream("libopus", rate=OGG_RATE)
        self._stream.codec_context.layout = layout_for(params.channels)
        self._stream.codec_context.format = "s16"
        self._stream.codec_context.extradata = opus_head(
            params.channels, params.sample_rate
        )
        self._time_base = fractions.Fraction(1, OGG_RATE)
        self._pts = 0
        self._silence: dict[int, bytes] = {}

    def _mux(self, data: bytes, duration_us: int) -> None:
        duration = round(duration_us * OGG_RATE / 1_000_000)
        packet = av.Packet(data)
        packet.pts = packet.dts = self._pts
        packet.duration = duration
        packet.time_base = self._time_base
        packet.stream = self._stream
        self._out.mux(packet)
        self._pts += duration

    def write(self, frame: AudioFrame) -> None:
        self._mux(frame.data, frame.duration_us)

    def _silent_packet(self, frame_ms: int) -> bytes:
        if frame_ms not in self._silence:
            enc = av.CodecContext.create("libopus", "w")
            enc.sample_rate = self.params.sample_rate
            enc.layout = layout_for(self.params.channels)
            enc.format = "s16"
            enc.time_base = fractions.Fraction(1, self.params.sample_rate)
            enc.options = {"frame_duration": str(frame_ms)}
            enc.open()
            samples = self.params.sample_rate * frame_ms // 1000
            frame = av.AudioFrame.from_ndarray(
                np.zeros((1, samples * self.params.channels), dtype=np.int16),
                format="s16",
                layout=layout_for(self.params.channels),
            )
            frame.sample_rate = self.params.sample_rate
            frame.pts = 0
            packets = enc.encode(frame) + enc.encode(None)
            self._silence[frame_ms] = bytes(packets[0])
        return self._silence[frame_ms]

    def silence(self, duration_us: int) -> None:
        # Whole 20 ms silent packets, then 10 ms for the remainder (rounded).
        for frame_ms in (20, 10):
            while duration_us >= frame_ms * 1000 - 2500:
                self._mux(self._silent_packet(frame_ms), frame_ms * 1000)
                duration_us -= frame_ms * 1000

    def close(self) -> None:
        self._out.close()


@dataclass
class ExportOptions:
    output_dir: Path
    format: str = "auto"  # auto | wav | ogg
    split_gap_s: float = 5.0
    start_ns: int | None = None
    end_ns: int | None = None


def _segment_class(params: Params, fmt: str) -> type[_Segment]:
    if params.format == PCM_S16LE:
        if fmt == "ogg":
            raise ValueError(
                "PCM frames cannot be exported as Ogg Opus without re-encoding"
            )
        return WavSegment
    if params.format == OPUS:
        return WavSegment if fmt == "wav" else OggOpusSegment
    raise ValueError(f"unsupported audio format {params.format}")


def export_frames(
    frames: Iterable[AudioFrame], slug: str, options: ExportOptions
) -> list[Path]:
    """Write one source's frames, already roughly in order, to files."""
    written: list[Path] = []
    segment: _Segment | None = None
    expected_ns: int | None = None
    split_ns = int(options.split_gap_s * 1e9)

    def close():
        nonlocal segment
        if segment is not None:
            segment.close()
            written.append(segment.path)
            segment = None

    try:
        for frame in in_capture_order(frames):
            ts = ns_of(frame)
            dur_ns = frame.duration_us * 1000
            params = params_of(frame)
            if segment is not None:
                gap = ts - expected_ns
                if params != segment.params or gap > split_ns:
                    close()
                elif gap < -dur_ns // 2:
                    continue  # overlaps audio already written: a duplicate
                elif gap >= dur_ns // 2:
                    segment.silence(gap // 1000)
            if segment is None:
                cls = _segment_class(params, options.format)
                path = options.output_dir / f"{slug}_{ns_to_stamp(ts)}{cls.suffix}"
                segment = cls(path, params)
                logger.info("Writing %s", path)
            segment.write(frame)
            expected_ns = ts + dur_ns
    finally:
        close()
    return written


def audio_channels(path: Path) -> dict[str, list[int]]:
    """Topics with an AudioFrame schema in an MCAP file, with their channel ids."""
    with open(path, "rb") as f:
        summary = make_reader(f).get_summary()
    if summary is None:
        raise ValueError(f"{path} has no summary section; it may be incomplete")
    topics: dict[str, list[int]] = {}
    for channel in summary.channels.values():
        schema = summary.schemas.get(channel.schema_id)
        if schema is not None and schema.name == SCHEMA_NAME:
            topics.setdefault(channel.topic, []).append(channel.id)
    return topics


def _read_frames(
    paths: list[Path], topic: str, options: ExportOptions
) -> Iterator[AudioFrame]:
    for path in paths:
        with open(path, "rb") as f:
            reader = make_reader(f)
            for _schema, _channel, message in reader.iter_messages(
                topics=[topic],
                start_time=options.start_ns,
                end_time=options.end_ns,
                log_time_order=True,
            ):
                frame = AudioFrame()
                frame.ParseFromString(message.data)
                yield frame


def export_mcap(
    paths: list[Path], options: ExportOptions, topic_filter: str | None = None
) -> list[Path]:
    """Export every audio source in ``paths`` (several files are one recording)."""
    options.output_dir.mkdir(parents=True, exist_ok=True)
    topics: set[str] = set()
    for path in paths:
        topics.update(audio_channels(path))
    if topic_filter:
        topics = {t for t in topics if re.search(topic_filter, t)}
    written = []
    for topic in sorted(topics):
        written += export_frames(
            _read_frames(paths, topic, options), source_slug(topic), options
        )
    return written
