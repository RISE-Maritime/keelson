"""Unit tests for audio_connector.export (MCAP -> .ogg/.wav)."""

import av
import numpy as np
import pytest
from mcap.writer import Writer

import keelson
from audio_connector import codec
from audio_connector.export import (
    ExportOptions,
    audio_channels,
    export_frames,
    export_mcap,
    in_capture_order,
    source_slug,
)

from audio_test_helpers import read_wav, sine

pytestmark = pytest.mark.unit

T0 = 1_750_000_000_000_000_000  # 2025-06-15T15:06:40Z
TOPIC = "rise/@v0/vessel/pubsub/audio_frame/audio/bridge"


def encode(seconds, fmt=codec.OPUS, start_ns=T0, drop=(), rate=48000):
    enc = codec.FrameEncoder(fmt, rate, 1, 20)
    pcm = sine(seconds, rate=rate)
    frames = []
    for i in range(len(pcm) // enc.frame_samples):
        block = pcm[i * enc.frame_samples : (i + 1) * enc.frame_samples]
        frames += enc.encode(block, start_ns + i * 20_000_000)
    return [f for f in frames if f.sequence not in drop]


def ogg_seconds(path):
    with av.open(str(path)) as c:
        return sum(f.samples for f in c.decode(audio=0)) / 48000


def test_source_slug():
    assert source_slug(TOPIC) == "vessel_audio-bridge"
    assert source_slug("weird topic!") == "weird-topic"


def test_capture_order_is_restored():
    frames = encode(0.2)
    shuffled = frames[1:3] + frames[:1] + frames[3:]
    assert [f.sequence for f in in_capture_order(shuffled)] == list(range(10))


def test_opus_exports_to_ogg_with_lost_frames_filled(temp_dir):
    frames = encode(1.0, drop={10, 11, 12})
    written = export_frames(frames, "src", ExportOptions(temp_dir))
    assert len(written) == 1 and written[0].suffix == ".ogg"
    assert written[0].name == "src_20250615T150640.000Z.ogg"
    assert ogg_seconds(written[0]) == pytest.approx(1.0, abs=0.03)


def test_long_gap_starts_a_new_file(temp_dir):
    frames = encode(0.5) + encode(0.5, start_ns=T0 + 60 * 10**9)
    written = export_frames(frames, "src", ExportOptions(temp_dir, split_gap_s=5))
    assert [p.name for p in written] == [
        "src_20250615T150640.000Z.ogg",
        "src_20250615T150740.000Z.ogg",
    ]


def test_opus_to_wav_and_duplicates_dropped(temp_dir):
    frames = encode(0.4)
    written = export_frames(
        frames + frames[:3], "src", ExportOptions(temp_dir, format="wav")
    )
    pcm, rate = read_wav(written[0])
    assert rate == 48000 and len(pcm) == pytest.approx(0.4 * 48000, abs=960)


def test_pcm_exports_to_wav_exactly(temp_dir):
    frames = encode(0.2, fmt=codec.PCM_S16LE, rate=16000)
    written = export_frames(frames, "src", ExportOptions(temp_dir))
    pcm, rate = read_wav(written[0])
    assert rate == 16000
    np.testing.assert_array_equal(pcm, sine(0.2, rate=16000))


def test_pcm_cannot_be_ogg(temp_dir):
    with pytest.raises(ValueError):
        export_frames(
            encode(0.1, fmt=codec.PCM_S16LE),
            "src",
            ExportOptions(temp_dir, format="ogg"),
        )


def write_mcap(path, topic, frames):
    fds = keelson.get_protobuf_file_descriptor_set_from_type_name("keelson.AudioFrame")
    with open(path, "wb") as f:
        writer = Writer(f)
        writer.start()
        schema = writer.register_schema(
            name="keelson.AudioFrame", encoding="protobuf", data=fds.SerializeToString()
        )
        channel = writer.register_channel(
            topic=topic, message_encoding="protobuf", schema_id=schema
        )
        for frame in frames:
            ts = frame.timestamp.ToNanoseconds()
            writer.add_message(
                channel,
                log_time=ts + 5_000_000,
                publish_time=ts,
                data=frame.SerializeToString(),
            )
        writer.finish()


def test_export_mcap_end_to_end(temp_dir):
    path = temp_dir / "rec.mcap"
    write_mcap(path, TOPIC, encode(1.0))
    assert list(audio_channels(path)) == [TOPIC]

    written = export_mcap([path], ExportOptions(temp_dir / "out"))
    assert [p.name for p in written] == ["vessel_audio-bridge_20250615T150640.000Z.ogg"]
    assert ogg_seconds(written[0]) == pytest.approx(1.0, abs=0.03)

    clipped = export_mcap(
        [path],
        ExportOptions(temp_dir / "clip", start_ns=T0 + 500_000_000),
    )
    assert ogg_seconds(clipped[0]) == pytest.approx(0.5, abs=0.03)

    assert (
        export_mcap([path], ExportOptions(temp_dir / "none"), topic_filter="engine")
        == []
    )
