"""
End-to-end test of the talk-back floor: keelson2audio --serve-floor on a real
Zenoh session, two claimants, and a PTT command that leaves a marker file
while the transmitter is keyed.
"""

import threading
import time
from pathlib import Path

import numpy as np
import pytest
import zenoh

import keelson
from keelson.interfaces import RpcError, invoke_procedure
from keelson.interfaces.AudioOutput_pb2 import (
    GetStateRequest,
    ReleaseFloorRequest,
    RequestFloorRequest,
)
from keelson.payloads.AudioOutputState_pb2 import AudioOutputState
from keelson.scaffolding import create_zenoh_config, declare_publisher

from audio_connector import codec

from .conftest import read_wav, sine

REALM = "test-realm"
VESSEL = "vessel"
OUTPUT = "audio/pa"
A_KEY = f"{REALM}/@v0/roc/pubsub/audio_frame/talkback/a"
B_KEY = f"{REALM}/@v0/roc/pubsub/audio_frame/talkback/b"
HELD, IDLE = AudioOutputState.FLOOR_HELD, AudioOutputState.FLOOR_IDLE
R = AudioOutputState.ReleaseReason


def call(session, procedure, request):
    return invoke_procedure(
        session,
        REALM,
        VESSEL,
        "audio_output",
        "v1",
        procedure,
        OUTPUT,
        request,
        timeout=3.0,
    )


def wait_for(predicate, timeout=5.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class States:
    """Every audio_output_state the sink publishes."""

    def __init__(self, session):
        self.seen: list[AudioOutputState] = []
        key = keelson.construct_pubsub_key(REALM, VESSEL, "audio_output_state", OUTPUT)
        self._sub = session.declare_subscriber(key, self._on)

    def _on(self, sample):
        _, _, payload = keelson.uncover(sample.payload.to_bytes())
        self.seen.append(AudioOutputState.FromString(payload))

    def latest(self):
        return self.seen[-1] if self.seen else None


def speak(session, key, seconds):
    """Publish a tone on ``key`` in real time, as a station would."""
    pub = declare_publisher(session, key)
    enc = codec.FrameEncoder(codec.OPUS, 48000, 1, 20)
    pcm = sine(seconds)
    start = time.monotonic()
    for i in range(len(pcm) // 960):
        for frame in enc.encode(pcm[i * 960 : (i + 1) * 960], time.time_ns()):
            pub.put(keelson.enclose(frame.SerializeToString()))
        delay = start + (i + 1) * 0.02 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    pub.undeclare()


@pytest.fixture
def station(zenoh_endpoints):
    conf = create_zenoh_config(mode="peer", listen=[zenoh_endpoints["listen"]])
    session = zenoh.open(conf)
    try:
        yield session
    finally:
        session.close()


@pytest.mark.e2e
def test_talkback_floor(
    connector_process_factory, station, temp_dir: Path, zenoh_endpoints
):
    marker = temp_dir / "ptt-keyed"
    out_wav = temp_dir / "pa.wav"
    sink = connector_process_factory(
        "audio",
        "keelson2audio.py",
        [
            "--serve-floor",
            "-r",
            REALM,
            "-e",
            VESSEL,
            "-s",
            OUTPUT,
            "-f",
            "pa_deck",
            "--output-file",
            str(out_wav),
            "--floor-silence-timeout",
            "1.0",
            "--max-transmit-seconds",
            "30",
            "--ptt-key-command",
            f"touch {marker}",
            "--ptt-unkey-command",
            f"rm -f {marker}",
            "--ptt-lead-ms",
            "100",
            "--ptt-tail-ms",
            "100",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    states = States(station)
    sink.start()
    try:
        # The sink comes up idle.
        def up():
            try:
                return call(station, "get_state", GetStateRequest()).floor == IDLE
            except (RpcError, TimeoutError):
                return False

        wait_for(up, timeout=15, what="the sink to serve audio_output/v1")
        assert call(station, "get_state", GetStateRequest()).frame_id == "pa_deck"

        # A takes the floor; PTT keys.
        state = call(
            station,
            "request_floor",
            RequestFloorRequest(claimant_id="a", source_key=A_KEY),
        )
        assert (
            state.floor == HELD and state.holder_id == "a" and state.source_key == A_KEY
        )
        assert state.silence_timeout_ms == 1000 and state.max_transmit_ms == 30000
        wait_for(marker.exists, what="PTT to key")

        # B at the same priority is refused, with a typed denial.
        with pytest.raises(RpcError) as exc:
            call(
                station,
                "request_floor",
                RequestFloorRequest(claimant_id="b", source_key=B_KEY),
            )
        assert exc.value.code_name == "PERMISSION_DENIED"

        # A speaks and is heard; B's frames are not played.
        noise = threading.Thread(target=speak, args=(station, B_KEY, 1.0))
        noise.start()
        speak(station, A_KEY, 1.0)
        noise.join()

        # B at a higher priority preempts A.
        state = call(
            station,
            "request_floor",
            RequestFloorRequest(claimant_id="b", source_key=B_KEY, priority=5),
        )
        assert state.holder_id == "b"
        assert state.last_release_reason == R.RELEASE_REASON_PREEMPTED
        assert state.last_holder_id == "a"

        # A's release is idempotent now: nothing changes.
        state = call(station, "release_floor", ReleaseFloorRequest(claimant_id="a"))
        assert state.holder_id == "b"

        # B never speaks: silence frees the floor, and PTT unkeys after the tail.
        wait_for(
            lambda: (s := states.latest()) is not None
            and s.floor == IDLE
            and s.last_release_reason == R.RELEASE_REASON_SILENCE,
            what="the silence release",
        )
        wait_for(lambda: not marker.exists(), what="PTT to unkey")

        # A takes it again, and the sink is stopped while A holds it.
        call(
            station,
            "request_floor",
            RequestFloorRequest(claimant_id="a", source_key=A_KEY),
        )
        wait_for(marker.exists, what="PTT to key again")
        sink.stop()
        assert not marker.exists(), "PTT left keyed after shutdown"
        wait_for(
            lambda: states.latest().last_release_reason == R.RELEASE_REASON_SHUTDOWN,
            what="the shutdown state",
        )
    finally:
        if sink.is_running():
            sink.stop()
        print(sink.logs()[1][-3000:])

    # Every state seen is either idle or held, and was published under our output.
    assert {s.floor for s in states.seen} == {HELD, IDLE}

    # What the PA played: A's second of tone (minus the PTT lead), not B's.
    pcm, rate = read_wav(out_wav)
    loud = np.abs(pcm[:, 0].astype(np.int32)) > 1000
    heard = loud.sum() / rate
    assert 0.6 <= heard <= 1.05, f"heard {heard:.2f} s of tone"
