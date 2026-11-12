#!/usr/bin/env python3

"""
Audio sink for Keelson: plays `audio_frame` streams live.

Subscribes to one or more key expressions, puts each source's frames back in
order through a jitter buffer, decodes them and mixes all sources into one
output: a sound device, or a WAV file written in real time (for headless
monitoring and for tests). A pure consumer: it declares no liveliness.

Replayed recordings play correctly at 1x. Faster than that, frames arrive
quicker than they play and the jitter buffer drops the excess, so the audio
is choppy; mute it instead. See docs/decisions/0002-audio.md.
"""

import argparse
import logging
import sys
import time
import wave

import zenoh

import keelson
from keelson.payloads.AudioFrame_pb2 import AudioFrame
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    setup_logging,
)

from audio_connector import sources
from audio_connector.playout import Mixer

logger = logging.getLogger("keelson2audio")

SUBJECT = "audio_frame"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keelson2audio",
        description="Play keelson.AudioFrame streams (subject audio_frame) on a sound "
        "device or into a WAV file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)
    parser.add_argument(
        "-k",
        "--key",
        action="append",
        help="Key expression to play, e.g. rise/@v0/vessel/pubsub/audio_frame/audio/bridge. "
        "Repeatable; every matching source is mixed in. Required unless --list-devices",
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
    return parser


def subscribe(session, keys, mixer: Mixer):
    def on_sample(sample) -> None:
        key = str(sample.key_expr)
        try:
            if keelson.get_subject_from_pubsub_key(key) != SUBJECT:
                return
            _, _, payload = keelson.uncover(sample.payload.to_bytes())
            frame = AudioFrame()
            frame.ParseFromString(payload)
        except Exception as exc:
            logger.debug("Ignoring sample on %s: %s", key, exc)
            return
        mixer.push(key, frame)

    subs = []
    for key in keys:
        subs.append(session.declare_subscriber(key, on_sample))
        logger.info("Listening on %s", key)
    return subs


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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    if args.list_devices:
        print(sources.list_devices())
        return 0
    if not args.key:
        parser.error("the following arguments are required: -k/--key")

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
    try:
        with GracefulShutdown() as shutdown:
            subs = subscribe(session, args.key, mixer)
            try:
                if args.output_file:
                    play_file(mixer, args, shutdown)
                else:
                    play_device(mixer, args, shutdown)
            finally:
                for sub in subs:
                    sub.undeclare()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
