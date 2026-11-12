"""Unit tests for audio_connector.codec."""

import numpy as np
import pytest

from audio_connector import codec

from .conftest import sine

pytestmark = pytest.mark.unit


def blocks(pcm, size):
    return [pcm[i : i + size] for i in range(0, len(pcm) - size + 1, size)]


def rms(pcm):
    return float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))


def test_opus_round_trip_keeps_length_and_level():
    enc = codec.FrameEncoder(codec.OPUS, 48000, 1, 20, frame_id="bridge")
    pcm = sine(1.0)
    frames = []
    for i, block in enumerate(blocks(pcm, enc.frame_samples)):
        frames += enc.encode(block, 1_000_000_000 + i * 20_000_000)
    frames += enc.flush()

    assert [f.sequence for f in frames] == list(range(len(frames)))
    assert all(f.format == codec.OPUS and f.sample_rate == 48000 for f in frames)
    assert all(f.channels == 1 and f.duration_us == 20000 for f in frames)
    assert frames[0].frame_id == "bridge"
    assert frames[0].timestamp.ToNanoseconds() == 1_000_000_000
    # Opus at 32 kbit/s: a 20 ms packet is ~80 bytes, not 1920 bytes of PCM.
    assert max(len(f.data) for f in frames) < 300

    dec = codec.FrameDecoder(48000, 1)
    out = np.concatenate([dec.decode(f) for f in frames])
    assert abs(len(out) - len(pcm)) <= enc.frame_samples
    # Skip the encoder's 6.5 ms lookahead; the level must survive.
    assert rms(out[960:-960]) == pytest.approx(rms(pcm), rel=0.15)


def test_one_packet_per_block_with_capture_timestamps():
    enc = codec.FrameEncoder(codec.OPUS, 48000, 1, 20)
    stamps = []
    for i, block in enumerate(blocks(sine(0.2), enc.frame_samples)):
        frames = enc.encode(block, i * 20_000_000)
        assert len(frames) == 1
        stamps.append(frames[0].timestamp.ToNanoseconds())
    assert stamps == [i * 20_000_000 for i in range(10)]


def test_pcm_round_trip_is_exact():
    enc = codec.FrameEncoder(codec.PCM_S16LE, 16000, 2, 10)
    pcm = sine(0.1, rate=16000, channels=2)
    frames = [f for b in blocks(pcm, enc.frame_samples) for f in enc.encode(b, 0)]
    assert len(frames[0].data) == enc.frame_samples * 2 * 2
    dec = codec.FrameDecoder(16000, 2)
    np.testing.assert_array_equal(np.concatenate([dec.decode(f) for f in frames]), pcm)
    assert enc.flush() == []


def test_decoder_resamples_to_output_format():
    enc = codec.FrameEncoder(codec.PCM_S16LE, 48000, 1, 20)
    frames = [f for b in blocks(sine(1.0), 960) for f in enc.encode(b, 0)]
    dec = codec.FrameDecoder(16000, 2)
    out = np.concatenate([dec.decode(f) for f in frames])
    assert out.shape[1] == 2
    # The resampler holds back a few samples; nearly a second at 16 kHz comes out.
    assert 15500 <= len(out) <= 16000


def test_silence_matches_duration():
    dec = codec.FrameDecoder(48000, 2)
    assert dec.silence(20000).shape == (960, 2)
    assert not dec.silence(20000).any()


def test_encode_rejects_wrong_block_shape():
    enc = codec.FrameEncoder(codec.OPUS, 48000, 1, 20)
    with pytest.raises(ValueError):
        enc.encode(np.zeros((480, 1), dtype=np.int16), 0)
    with pytest.raises(ValueError):
        enc.encode(np.zeros((960, 1), dtype=np.float32), 0)


@pytest.mark.parametrize(
    "fmt, rate, channels, frame_ms",
    [
        (codec.OPUS, 44100, 1, 20),  # not an Opus rate
        (codec.OPUS, 48000, 1, 25),  # not an Opus frame length
        (codec.OPUS, 48000, 6, 20),  # needs a channel map
        (codec.PCM_S16LE, 44100, 1, 3),  # 132.3 samples
        (codec.PCM_S16LE, 48000, 0, 20),
    ],
)
def test_validate_rejects(fmt, rate, channels, frame_ms):
    with pytest.raises(ValueError):
        codec.validate(fmt, rate, channels, frame_ms)


def test_pcm_allows_any_rate():
    codec.validate(codec.PCM_S16LE, 96000, 4, 20)


def test_decoder_rejects_unknown_format():
    from keelson.payloads.AudioFrame_pb2 import AudioFrame

    with pytest.raises(ValueError):
        codec.FrameDecoder(48000, 1).decode(AudioFrame(sample_rate=48000, channels=1))


def test_opus_head_layout():
    head = codec.opus_head(2, 48000)
    assert len(head) == 19 and head.startswith(b"OpusHead")
    assert head[8] == 1 and head[9] == 2
