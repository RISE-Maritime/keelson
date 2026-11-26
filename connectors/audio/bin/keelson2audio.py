#!/usr/bin/env python3

"""
Audio sink for Keelson: plays `audio_frame` streams live.

Subscribes to one or more key expressions, puts each source's frames back in
order through a jitter buffer, decodes them and mixes all sources into one
output: a sound device, or a WAV file written in real time (for headless
monitoring and for tests).

Two modes:

- **Listen** (`-k KEY`): a pure consumer. It declares no liveliness.
- **Talk-back** (`--serve-floor`): the sink is an output on board (PA,
  intercom, VHF transmit) and the only arbiter of its floor
  (protocols/audio_floor.yaml). It serves `audio_output/v1`, plays only the
  floor holder's stream, publishes `audio_output_state`, and keys PTT itself.
  The floor is released when the holder's stream falls silent or the hard
  time limit passes, and PTT is unkeyed on every exit path.

Replayed recordings play correctly at 1x. Faster than that, frames arrive
quicker than they play and the jitter buffer drops the excess, so the audio
is choppy; mute it instead. See docs/decisions/0002-audio.md.
"""

import argparse
import logging
import queue
import sys
import threading
import time
import wave

import zenoh

import keelson
from keelson.interfaces.AudioOutput_pb2 import (
    ReleaseFloorRequest,
    RequestFloorRequest,
)
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.payloads.AudioFrame_pb2 import AudioFrame
from keelson.payloads.AudioOutputState_pb2 import AudioOutputState
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    serve_rpc,
    setup_logging,
)

from audio_connector import ptt, sources
from audio_connector.floor import Change, FloorArbiter, FloorRefused, Refusal
from audio_connector.playout import Mixer

logger = logging.getLogger("keelson2audio")

SUBJECT = "audio_frame"
STATE_SUBJECT = "audio_output_state"
INTERFACE, VERSION = "audio_output", "v1"
TICK_S = 0.05


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keelson2audio",
        description="Play keelson.AudioFrame streams (subject audio_frame) on a sound "
        "device or into a WAV file, optionally as an on-board talk-back output",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)
    parser.add_argument(
        "-k",
        "--key",
        action="append",
        help="Key expression to play, e.g. "
        "rise/@v0/vessel/pubsub/audio_frame/audio/bridge. Repeatable; every matching "
        "source is mixed in. Required unless --serve-floor or --list-devices",
    )
    out = parser.add_mutually_exclusive_group()
    out.add_argument(
        "--device",
        help='Output device: an index or (part of) a name from --list-devices, or "default"',
    )
    out.add_argument(
        "--output-file",
        help="Write the mix to this WAV file in real time instead of playing it",
    )
    out.add_argument(
        "--list-devices", action="store_true", help="Print the sound devices and exit"
    )
    parser.add_argument(
        "--sample-rate", type=int, default=48000, help="Output rate, Hz"
    )
    parser.add_argument("--channels", type=int, default=1, help="Output channels")
    parser.add_argument(
        "--block-ms", type=int, default=10, help="Output block length; the playout tick"
    )
    parser.add_argument(
        "--jitter-frames",
        type=int,
        default=3,
        help="Frames buffered before playout starts: absorbs network jitter, adds latency",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=25,
        help="Frames buffered at most per source; beyond this the oldest are dropped",
    )

    floor = parser.add_argument_group(
        "talk-back", "Serve audio_output/v1 and play only the floor holder"
    )
    floor.add_argument(
        "--serve-floor",
        action="store_true",
        help="Act as an on-board output whose floor stations request over RPC",
    )
    floor.add_argument("-r", "--realm", help="Realm (e.g. rise). Talk-back only")
    floor.add_argument(
        "-e", "--entity-id", help="Entity id of the vessel. Talk-back only"
    )
    floor.add_argument(
        "-s",
        "--source-id",
        help="Source id of this output, e.g. audio/pa_deck. Talk-back only",
    )
    floor.add_argument(
        "-f", "--frame-id", help="What this output is, e.g. pa_deck or vhf_ch16_tx"
    )
    floor.add_argument(
        "--floor-silence-timeout",
        type=float,
        default=3.0,
        help="Seconds without frames from the holder before the floor is released",
    )
    floor.add_argument(
        "--max-transmit-seconds",
        type=float,
        default=120.0,
        help="Hard limit on one grant, whatever the holder does",
    )
    floor.add_argument(
        "--state-interval",
        type=float,
        default=1.0,
        help="Seconds between audio_output_state publications when nothing changes",
    )

    keying = parser.add_argument_group(
        "push-to-talk", "Key a transmitter while the floor is held (talk-back only)"
    )
    keying.add_argument(
        "--ptt-serial",
        help="Serial port whose RTS/DTR line keys PTT, e.g. /dev/ttyUSB0",
    )
    keying.add_argument(
        "--ptt-line", choices=["RTS", "DTR"], default="RTS", help="Line to assert"
    )
    keying.add_argument("--ptt-key-command", help="Command that keys PTT")
    keying.add_argument("--ptt-unkey-command", help="Command that unkeys PTT")
    keying.add_argument(
        "--ptt-lead-ms",
        type=int,
        default=150,
        help="Audio is held back this long after keying, while the transmitter comes up",
    )
    keying.add_argument(
        "--ptt-tail-ms",
        type=int,
        default=300,
        help="PTT stays keyed this long after the floor is released, to drain the buffer",
    )
    return parser


def parse_frame(sample):
    """``(key, AudioFrame)`` from an audio_frame sample, or None."""
    key = str(sample.key_expr)
    try:
        if keelson.get_subject_from_pubsub_key(key) != SUBJECT:
            return None
        _, _, payload = keelson.uncover(sample.payload.to_bytes())
        frame = AudioFrame()
        frame.ParseFromString(payload)
    except Exception as exc:
        logger.debug("Ignoring sample on %s: %s", key, exc)
        return None
    return key, frame


def subscribe(session, keys, mixer: Mixer):
    def on_sample(sample) -> None:
        parsed = parse_frame(sample)
        if parsed is not None:
            mixer.push(*parsed)

    subs = []
    for key in keys:
        subs.append(session.declare_subscriber(key, on_sample))
        logger.info("Listening on %s", key)
    return subs


def make_ptt_device(args):
    if args.ptt_serial:
        return ptt.SerialPtt(args.ptt_serial, args.ptt_line)
    if args.ptt_key_command:
        return ptt.CommandPtt(args.ptt_key_command, args.ptt_unkey_command)
    return ptt.NoPtt()


class FloorService:
    """The talk-back floor of this output, on the bus.

    The arbiter reports changes under its lock; they are queued and applied by
    :meth:`step` on the main thread, which is the only place subscriptions are
    declared or undeclared, PTT is driven and state is published.
    """

    def __init__(self, session, args, mixer: Mixer, ptt_device):
        self._session = session
        self._mixer = mixer
        self._frame_id = args.frame_id or ""
        self._interval = args.state_interval
        self._changes: queue.Queue[Change] = queue.Queue()
        self._dirty = threading.Event()
        self._holder_sub = None
        self._last_publish = 0.0
        self.arbiter = FloorArbiter(
            silence_timeout_s=args.floor_silence_timeout,
            max_transmit_s=args.max_transmit_seconds,
            on_change=self._changes.put,
        )
        has_transmitter = not isinstance(ptt_device, ptt.NoPtt)
        self.ptt = ptt.PttDriver(
            ptt_device,
            lead_s=args.ptt_lead_ms / 1000 if has_transmitter else 0.0,
            tail_s=args.ptt_tail_ms / 1000 if has_transmitter else 0.0,
            on_change=lambda keyed: self._dirty.set(),
        )
        key = keelson.construct_pubsub_key(
            base_path=args.realm,
            entity_id=args.entity_id,
            subject=STATE_SUBJECT,
            source_id=args.source_id,
        )
        self._publisher = declare_publisher(session, key)
        self.server = serve_rpc(
            session,
            base_path=args.realm,
            entity_id=args.entity_id,
            responder_id=args.source_id,
            interface=INTERFACE,
            version=VERSION,
            handlers={
                "request_floor": self._request_floor,
                "release_floor": self._release_floor,
                "get_state": self._get_state,
            },
            summarizers={
                "request_floor": lambda b: _summary(RequestFloorRequest, b),
                "release_floor": lambda b: _summary(ReleaseFloorRequest, b),
            },
            log=logger,
        )
        logger.info("Serving %s/%s; state on %s", INTERFACE, VERSION, key)
        self._dirty.set()

    # --- state -----------------------------------------------------------

    def state(self) -> AudioOutputState:
        arbiter = self.arbiter
        msg = AudioOutputState(
            frame_id=self._frame_id,
            floor=AudioOutputState.FLOOR_IDLE,
            ptt_keyed=self.ptt.keyed,
            last_release_reason=arbiter.last_reason,
            last_holder_id=arbiter.last_holder_id,
            silence_timeout_ms=round(arbiter.silence_timeout_s * 1000),
            max_transmit_ms=round(arbiter.max_transmit_s * 1000),
        )
        msg.timestamp.FromNanoseconds(time.time_ns())
        grant = arbiter.grant
        if grant is not None:
            msg.floor = AudioOutputState.FLOOR_HELD
            msg.holder_id = grant.holder_id
            msg.source_key = grant.source_key
            msg.priority = grant.priority
            msg.granted_at.FromNanoseconds(int(grant.granted_at * 1e9))
            msg.expires_at.FromNanoseconds(
                int((grant.granted_at + arbiter.max_transmit_s) * 1e9)
            )
        return msg

    def _publish(self) -> None:
        self._publisher.put(keelson.enclose(self.state().SerializeToString()))
        self._last_publish = time.monotonic()

    # --- RPC -------------------------------------------------------------

    def _request_floor(self, op) -> None:
        try:
            req = RequestFloorRequest.FromString(op.request_bytes)
        except Exception as exc:
            op.reply_err(f"malformed request: {exc}", ErrorResponse.INVALID_ARGUMENT)
            return
        try:
            self.arbiter.request(req.claimant_id, req.source_key, req.priority)
        except FloorRefused as exc:
            code = (
                ErrorResponse.PERMISSION_DENIED
                if exc.kind is Refusal.BUSY
                else ErrorResponse.INVALID_ARGUMENT
            )
            op.reply_err(str(exc), code)
            return
        op.reply_ok(self.state())

    def _release_floor(self, op) -> None:
        try:
            req = ReleaseFloorRequest.FromString(op.request_bytes)
        except Exception as exc:
            op.reply_err(f"malformed request: {exc}", ErrorResponse.INVALID_ARGUMENT)
            return
        self.arbiter.release(req.claimant_id)
        op.reply_ok(self.state())

    def _get_state(self, op) -> None:
        op.reply_ok(self.state())

    # --- main-thread work ------------------------------------------------

    def _on_holder_sample(self, sample) -> None:
        parsed = parse_frame(sample)
        if parsed is None:
            return
        key, frame = parsed
        self.arbiter.frame(key)
        # Before the transmitter is up (the PTT lead) the audio would be clipped.
        if self.ptt.audio_open():
            self._mixer.push(key, frame)

    def _apply(self, change: Change) -> None:
        prev, cur = change.previous, change.current
        prev_key = prev.source_key if prev else None
        cur_key = cur.source_key if cur else None
        if prev_key != cur_key:
            if self._holder_sub is not None:
                self._holder_sub.undeclare()
                self._holder_sub = None
            if cur_key:
                self._holder_sub = self._session.declare_subscriber(
                    cur_key, self._on_holder_sample
                )
        if cur is not None:
            logger.info("Floor held by %s on %s", cur.holder_id, cur.source_key)
            self.ptt.floor_held()
        else:
            reason = AudioOutputState.ReleaseReason.Name(change.reason or 0)
            logger.info("Floor released by %s (%s)", prev.holder_id, reason)
            self.ptt.floor_free()
        self._dirty.set()

    def step(self) -> None:
        self.arbiter.tick()
        while True:
            try:
                change = self._changes.get_nowait()
            except queue.Empty:
                break
            self._apply(change)
        self.ptt.tick()
        if (
            self._dirty.is_set()
            or time.monotonic() - self._last_publish >= self._interval
        ):
            self._dirty.clear()
            self._publish()

    def close(self) -> None:
        """Release the floor, unkey PTT at once (no tail) and say so."""
        self.arbiter.shutdown()
        try:
            self.step()
        finally:
            self.ptt.close()
            if self._holder_sub is not None:
                self._holder_sub.undeclare()
                self._holder_sub = None
            self._publish()


def _summary(cls, data: bytes) -> str:
    try:
        msg = cls.FromString(data)
    except Exception:
        return "<unparseable>"
    return " ".join(f"{f.name}={v}" for f, v in msg.ListFields())


def play_device(mixer: Mixer, args, shutdown) -> None:
    import sounddevice as sd

    block = args.sample_rate * args.block_ms // 1000

    def callback(outdata, frames, time_info, status) -> None:
        if status.output_underflow:
            logger.debug("Output underflow")
        outdata[:] = mixer.read(frames)

    with sd.OutputStream(
        device=sources.parse_device(args.device),
        samplerate=args.sample_rate,
        channels=args.channels,
        dtype="int16",
        blocksize=block,
        latency="low",
        callback=callback,
    ):
        while not shutdown.wait(1.0):
            pass


def play_file(mixer: Mixer, args, shutdown) -> None:
    """Pull blocks on a wall-clock schedule, as a device would, into a WAV file."""
    block = args.sample_rate * args.block_ms // 1000
    period = args.block_ms / 1000
    with wave.open(args.output_file, "wb") as wav:
        wav.setnchannels(args.channels)
        wav.setsampwidth(2)
        wav.setframerate(args.sample_rate)
        next_tick = time.monotonic()
        while not shutdown.is_requested():
            wav.writeframes(mixer.read(block).astype("<i2").tobytes())
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                shutdown.wait(delay)
            elif delay < -1.0:  # fell far behind (suspended?): don't try to catch up
                next_tick = time.monotonic()
    logger.info("Wrote %s", args.output_file)


def run_output(mixer: Mixer, args, shutdown) -> None:
    """The output side, on its own thread. A failure stops the whole sink."""
    try:
        if args.output_file:
            play_file(mixer, args, shutdown)
        else:
            play_device(mixer, args, shutdown)
    except Exception:
        logger.exception("Audio output failed")
        shutdown.request()


def check_args(parser, args) -> None:
    if args.serve_floor:
        missing = [
            flag
            for flag, value in (
                ("--realm", args.realm),
                ("--entity-id", args.entity_id),
                ("--source-id", args.source_id),
            )
            if not value
        ]
        if missing:
            parser.error(f"--serve-floor requires {', '.join(missing)}")
        if args.ptt_serial and args.ptt_key_command:
            parser.error("--ptt-serial and --ptt-key-command are alternatives")
        if bool(args.ptt_key_command) != bool(args.ptt_unkey_command):
            parser.error("--ptt-key-command and --ptt-unkey-command go together")
        if args.floor_silence_timeout <= 0 or args.max_transmit_seconds <= 0:
            parser.error("the floor timeouts must be positive")
    else:
        if not args.key:
            parser.error("the following arguments are required: -k/--key")
        if args.ptt_serial or args.ptt_key_command:
            parser.error("PTT needs --serve-floor: only the floor holder is keyed")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    if args.list_devices:
        print(sources.list_devices())
        return 0
    check_args(parser, args)

    # Open the transmitter first: a PTT line that cannot be opened is fatal
    # before anything is advertised.
    ptt_device = make_ptt_device(args) if args.serve_floor else None
    mixer = Mixer(
        args.sample_rate,
        args.channels,
        target_frames=args.jitter_frames,
        max_frames=args.max_frames,
    )
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )
    session = zenoh.open(conf)
    floor = None
    try:
        with GracefulShutdown() as shutdown:
            subs = subscribe(session, args.key or [], mixer)
            output = threading.Thread(
                target=run_output, args=(mixer, args, shutdown), name="output"
            )
            output.start()
            try:
                if args.serve_floor:
                    with declare_liveliness(
                        session,
                        args.realm,
                        args.entity_id,
                        args.source_id,
                        pubsub_subjects=[STATE_SUBJECT],
                    ):
                        floor = FloorService(session, args, mixer, ptt_device)
                        while not shutdown.wait(TICK_S):
                            floor.step()
                        floor.close()
                        floor = ptt_device = None  # closed; nothing left to unkey
                else:
                    shutdown.wait()
            finally:
                shutdown.request()
                output.join(timeout=5)
                for sub in subs:
                    sub.undeclare()
    finally:
        # Fail-safe: whatever went wrong above, the transmitter is unkeyed.
        if floor is not None:
            floor.ptt.close()
        elif ptt_device is not None:
            ptt_device.close()
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
