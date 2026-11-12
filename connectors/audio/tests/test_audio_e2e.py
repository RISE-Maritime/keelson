"""
End-to-end tests for the audio connector over a real Zenoh session.

1. capture -> record -> export: a WAV file through audio2keelson, recorded by
   keelson2mcap, exported again by audio-export.
2. capture -> live playout: audio2keelson into keelson2audio --output-file.
"""

import time

import av
import numpy as np
import pytest
from mcap.reader import make_reader

from keelson.payloads.AudioFrame_pb2 import AudioFrame

from .conftest import read_wav

REALM = "test-realm"
ENTITY = "test-vessel"
SOURCE = "audio/bridge"


def _capture(factory, wav, endpoints):
    return factory(
        "audio",
        "audio2keelson.py",
        [
            "--realm",
            REALM,
            "--entity-id",
            ENTITY,
            "--source-id",
            SOURCE,
            "--frame-id",
            "bridge",
            "--url",
            str(wav),
            "--mode",
            "peer",
            "--connect",
            endpoints["connect"],
        ],
    )


def _wait_exit(proc, timeout=20.0):
    deadline = time.monotonic() + timeout
    while proc.is_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not proc.is_running(), "audio2keelson did not finish the file"
    return proc.process.returncode


@pytest.mark.e2e
def test_capture_record_export(
    connector_process_factory, run_connector, temp_dir, sine_wav, zenoh_endpoints
):
    rec_dir = temp_dir / "rec"
    rec_dir.mkdir()
    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            f"{REALM}/@v0/**",
            "--output-folder",
            str(rec_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    capture = _capture(connector_process_factory, sine_wav, zenoh_endpoints)
    capture.start()
    code = _wait_exit(capture)
    assert code == 0, capture.logs()[1]
    time.sleep(1)
    recorder.stop()

    files = list(rec_dir.glob("*.mcap"))
    assert len(files) == 1, recorder.logs()[1]

    # The recording holds the whole file as contiguous 20 ms Opus frames.
    with open(files[0], "rb") as f:
        frames, topics = [], set()
        for schema, channel, message in make_reader(f).iter_messages():
            if schema is not None and schema.name == "keelson.AudioFrame":
                frame = AudioFrame()
                frame.ParseFromString(message.data)
                frames.append(frame)
                topics.add(channel.topic)
    assert topics == {f"{REALM}/@v0/{ENTITY}/pubsub/audio_frame/{SOURCE}"}
    seqs = sorted(f.sequence for f in frames)
    assert seqs == list(range(len(seqs))), "frames were lost on loopback"
    assert 100 <= len(seqs) <= 102  # 2 s of 20 ms frames, plus the encoder tail
    assert all(f.frame_id == "bridge" and f.format == AudioFrame.OPUS for f in frames)

    out_dir = temp_dir / "out"
    result = run_connector(
        "audio", "audio-export.py", [str(files[0]), "-o", str(out_dir)]
    )
    assert result.returncode == 0, result.stderr
    exported = list(out_dir.glob("*.ogg"))
    assert len(exported) == 1
    assert exported[0].name.startswith("test-vessel_audio-bridge_")
    with av.open(str(exported[0])) as c:
        seconds = sum(fr.samples for fr in c.decode(audio=0)) / 48000
    assert seconds == pytest.approx(2.0, abs=0.05)


@pytest.mark.e2e
def test_capture_to_live_playout(
    connector_process_factory, temp_dir, sine_wav, zenoh_endpoints
):
    out_wav = temp_dir / "heard.wav"
    sink = connector_process_factory(
        "audio",
        "keelson2audio.py",
        [
            "--key",
            f"{REALM}/@v0/{ENTITY}/pubsub/audio_frame/**",
            "--output-file",
            str(out_wav),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    sink.start()
    time.sleep(1)

    capture = _capture(connector_process_factory, sine_wav, zenoh_endpoints)
    capture.start()
    assert _wait_exit(capture) == 0, capture.logs()[1]
    time.sleep(0.5)
    sink.stop()

    pcm, rate = read_wav(out_wav)
    assert rate == 48000
    loud = np.abs(pcm[:, 0].astype(np.int32)) > 1000
    assert loud.any(), "nothing was heard"
    # The tone plays for its full two seconds (give or take the codec's edges).
    first, last = np.argmax(loud), len(loud) - np.argmax(loud[::-1])
    assert (last - first) / rate == pytest.approx(2.0, abs=0.1)
    tone = pcm[first + 4800 : last - 4800, 0].astype(np.float64)
    assert np.sqrt(np.mean(tone**2)) == pytest.approx(8000 / np.sqrt(2), rel=0.15)
