"""Unit tests for audio_connector.sources (no sound device needed)."""

import threading
import time

import numpy as np
import pytest

from audio_connector import sources

from audio_test_helpers import sine, write_wav

pytestmark = pytest.mark.unit


def test_live_url_detection():
    assert sources.is_live_url("rtsp://host:8554/bridge")
    assert sources.is_live_url("srt://host:9000")
    assert not sources.is_live_url("/data/bridge.wav")
    assert not sources.is_live_url("file:///data/bridge.wav")


def test_parse_device():
    assert sources.parse_device(None) is None
    assert sources.parse_device("default") is None
    assert sources.parse_device("3") == 3
    assert sources.parse_device("USB Audio") == "USB Audio"


def test_timeline_is_evenly_spaced_despite_bursts():
    tl = sources.Timeline(48000)
    # Three 20 ms blocks arriving in one burst at t=1 s.
    stamps = [tl.stamp(960, now_ns=1_000_000_000) for _ in range(3)]
    assert stamps == [980_000_000, 1_000_000_000, 1_020_000_000]


def test_timeline_reanchors_after_a_stall():
    tl = sources.Timeline(48000)
    tl.stamp(960, now_ns=1_000_000_000)
    ts = tl.stamp(960, now_ns=3_000_000_000)  # 2 s later, one block on
    assert ts == 2_980_000_000


def collect(url, **kw):
    out = []
    sources.capture_url(
        str(url),
        on_block=lambda ts, block: out.append((ts, block)),
        stop=threading.Event(),
        **kw,
    )
    return out


def test_capture_url_cuts_a_file_into_blocks(temp_dir):
    path = write_wav(temp_dir / "s.wav", sine(0.5), 48000)
    got = collect(path, sample_rate=48000, channels=1, frame_samples=960, pace=False)
    assert len(got) == 25
    assert all(block.shape == (960, 1) and block.dtype == np.int16 for _, block in got)
    stamps = [ts for ts, _ in got]
    assert all(b - a == 20_000_000 for a, b in zip(stamps, stamps[1:]))


def test_capture_url_resamples_and_pads_the_last_block(temp_dir):
    # 0.51 s at 44.1 kHz stereo -> 16 kHz mono in 20 ms blocks: 25.5 blocks.
    path = write_wav(temp_dir / "s.wav", sine(0.51, rate=44100, channels=2), 44100)
    got = collect(path, sample_rate=16000, channels=1, frame_samples=320, pace=False)
    assert len(got) == 26
    assert got[-1][1].shape == (320, 1)
    assert not got[-1][1][200:].any()


def test_capture_url_paces_files_to_real_time(temp_dir):
    path = write_wav(temp_dir / "s.wav", sine(0.3), 48000)
    start = time.monotonic()
    got = collect(path, sample_rate=48000, channels=1, frame_samples=960, pace=True)
    assert len(got) == 15
    assert time.monotonic() - start >= 0.25


def test_capture_url_rejects_a_file_without_audio(temp_dir):
    path = temp_dir / "not-audio.txt"
    path.write_text("hello")
    with pytest.raises(Exception):
        collect(path, sample_rate=48000, channels=1, frame_samples=960, pace=False)
