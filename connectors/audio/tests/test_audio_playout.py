"""Unit tests for audio_connector.playout (the sink's mixer)."""

import numpy as np
import pytest

from keelson.payloads.AudioFrame_pb2 import AudioFrame

from audio_connector import codec
from audio_connector.playout import Mixer

pytestmark = pytest.mark.unit


def pcm_frame(seq, value, samples=960, rate=48000):
    return AudioFrame(
        format=codec.PCM_S16LE,
        sample_rate=rate,
        channels=1,
        sequence=seq,
        duration_us=samples * 1_000_000 // rate,
        data=np.full(samples, value, dtype="<i2").tobytes(),
    )


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_plays_in_order_after_prefill():
    mixer = Mixer(48000, 1, target_frames=2)
    mixer.push("a", pcm_frame(1, 200))
    assert not mixer.read(480).any()  # prefilling
    mixer.push("a", pcm_frame(0, 100))
    out = mixer.read(1920)
    assert (out[:960] == 100).all() and (out[960:] == 200).all()


def test_reads_across_frame_boundaries():
    mixer = Mixer(48000, 1, target_frames=1)
    for seq in range(3):
        mixer.push("a", pcm_frame(seq, seq + 1))
    out = np.concatenate([mixer.read(480) for _ in range(6)])
    assert list(out[::480, 0]) == [1, 1, 2, 2, 3, 3]


def test_gap_plays_silence_for_the_missing_frame():
    mixer = Mixer(48000, 1, target_frames=2)
    for seq in (0, 2):
        mixer.push("a", pcm_frame(seq, 50))
    out = mixer.read(960 * 3)
    assert (out[:960] == 50).all()
    assert not out[960:1920].any()
    assert (out[1920:] == 50).all()


def test_sources_are_mixed_and_clipped():
    mixer = Mixer(48000, 1, target_frames=1)
    mixer.push("a", pcm_frame(0, 30000))
    mixer.push("b", pcm_frame(0, 30000))
    out = mixer.read(960)
    assert (out == 32767).all()
    assert sorted(mixer.sources) == ["a", "b"]


def test_output_is_resampled_to_the_device_format():
    mixer = Mixer(16000, 2, target_frames=1)
    for seq in range(5):
        mixer.push("a", pcm_frame(seq, 1000))
    out = mixer.read(1000)
    assert out.shape == (1000, 2)
    # FFmpeg upmixes mono to stereo at -3 dB per channel (pan law): 1000 -> 707.
    assert (np.abs(out[100:900] - 707) <= 2).all()


def test_quiet_source_is_forgotten():
    clock = FakeClock()
    mixer = Mixer(48000, 1, target_frames=1, idle_timeout_s=5.0, clock=clock)
    mixer.push("a", pcm_frame(0, 1))
    mixer.read(960)
    clock.now = 10.0
    mixer.read(960)
    assert mixer.sources == []


def test_undecodable_frame_becomes_silence():
    mixer = Mixer(48000, 1, target_frames=1)
    bad = AudioFrame(
        format=codec.OPUS,
        sample_rate=44100,
        channels=1,
        sequence=0,
        duration_us=20000,
        data=b"\x00",
    )
    mixer.push("a", bad)
    out = mixer.read(960)
    assert out.shape == (960, 1)
