#!/usr/bin/env python3

"""
Audio capture connector for Keelson.

Captures audio from a sound device (PortAudio) or from any URL or file PyAV
can open (RTSP, a MediaMTX path, a WAV file, ...), cuts it into fixed-length
frames, encodes them (Opus by default) and publishes each as a
keelson.AudioFrame on `audio_frame`. The same frames serve live listening
(keelson2audio) and recording (keelson2mcap). See docs/decisions/0002-audio.md.
"""

import argparse
import logging
import queue
import sys
import threading
import time

import zenoh

import keelson
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    check_queue_backpressure,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    setup_logging,
)

from audio_connector import codec, sources

logger = logging.getLogger("audio2keelson")

SUBJECT = "audio_frame"
PUBSUB_SUBJECTS = [SUBJECT]

# Blocks waiting to be encoded. A device callback must never block, so a full
# queue drops the newest block and counts it.
QUEUE_BLOCKS = 250  # 5 s at 20 ms
QUEUE_WARN, QUEUE_ERROR = 25, 150


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio2keelson",
        description="Capture audio from a sound device or URL and publish it as "
        "keelson.AudioFrame on the audio_frame subject",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)
    # Required unless --list-devices; checked after parsing.
    parser.add_argument("-r", "--realm", help="Realm (e.g. rise). Required")
    parser.add_argument(
        "-e", "--entity-id", help="Entity id (e.g. landkrabba). Required"
    )
    parser.add_argument(
        "-s", "--source-id", help="Source id (e.g. audio/bridge). Required"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--device",
        help='Input device: an index or (part of) a name from --list-devices, or "default"',
    )
    src.add_argument(
        "--url",
        help="Any URL or file PyAV/FFmpeg can open (rtsp://..., a .wav file, ...)",
    )
    src.add_argument(
        "--list-devices", action="store_true", help="Print the sound devices and exit"
    )
    parser.add_argument(
        "--format",
        choices=sorted(codec.FORMATS),
        default="opus",
        help="Encoding on the bus. pcm_s16le is for sources whose samples are the "
        "measurement (hydrophones), not for speech",
    )
    parser.add_argument("--sample-rate", type=int, default=48000, help="Hz")
    parser.add_argument(
        "--channels", type=int, default=1, help="Channel count (Opus: 1 or 2)"
    )
    parser.add_argument("--frame-ms", type=int, default=20, help="Frame length")
    parser.add_argument(
        "--bitrate", type=int, default=32000, help="Opus bitrate in bit/s"
    )
    parser.add_argument(
        "-f",
        "--frame-id",
        help="What the source listens to, e.g. bridge or vhf_ch16_rx",
    )
    # SUPPRESS: the default depends on the input, so there is none to print.
    pace = parser.add_mutually_exclusive_group()
    pace.add_argument(
        "--pace",
        dest="pace",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Hold --url input to real time (the default for files)",
    )
    pace.add_argument(
        "--no-pace",
        dest="pace",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Publish --url input as fast as it decodes (the default for network streams)",
    )
    return parser


def run(session, args, shutdown) -> int:
    fmt = codec.FORMATS[args.format]
    encoder = codec.FrameEncoder(
        fmt,
        args.sample_rate,
        args.channels,
        args.frame_ms,
        bitrate=args.bitrate,
        frame_id=args.frame_id or "",
    )
    key = keelson.construct_pubsub_key(
        base_path=args.realm,
        entity_id=args.entity_id,
        subject=SUBJECT,
        source_id=args.source_id,
    )
    publisher = declare_publisher(session, key)
    logger.info(
        "Publishing %s %d Hz x%d, %d ms frames on %s",
        args.format,
        args.sample_rate,
        args.channels,
        args.frame_ms,
        key,
    )

    blocks: queue.Queue = queue.Queue(maxsize=QUEUE_BLOCKS)
    dropped = 0
    capture_done = threading.Event()
    capture_error: list[BaseException] = []

    def on_block(ts: int, block) -> None:
        nonlocal dropped
        try:
            blocks.put_nowait((ts, block))
        except queue.Full:
            dropped += 1

    def publish(frames) -> None:
        for frame in frames:
            publisher.put(keelson.enclose(frame.SerializeToString()))

    stop = threading.Event()
    device = None
    if args.url:

        def _capture():
            try:
                sources.capture_url(
                    args.url,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                    frame_samples=encoder.frame_samples,
                    on_block=on_block,
                    stop=stop,
                    pace=getattr(args, "pace", None),
                )
            except BaseException as exc:  # reported on the main thread
                capture_error.append(exc)
            finally:
                capture_done.set()

        thread = threading.Thread(target=_capture, name="capture", daemon=True)
        thread.start()
    else:
        device = sources.DeviceCapture(
            sources.parse_device(args.device),
            sample_rate=args.sample_rate,
            channels=args.channels,
            frame_samples=encoder.frame_samples,
            on_block=on_block,
        )
        device.__enter__()

    published = 0
    last_report = time.monotonic()
    try:
        while not shutdown.is_requested():
            try:
                ts, block = blocks.get(timeout=0.1)
            except queue.Empty:
                if capture_done.is_set() and blocks.empty():
                    break
                continue
            frames = encoder.encode(block, ts)
            publish(frames)
            published += len(frames)

            now = time.monotonic()
            if now - last_report >= 10:
                check_queue_backpressure(
                    blocks, QUEUE_WARN, QUEUE_ERROR, context="audio encoder"
                )
                logger.info(
                    "Published %d frames (%d blocks dropped)", published, dropped
                )
                last_report = now
        tail = encoder.flush()
        publish(tail)
        published += len(tail)
    finally:
        stop.set()
        if device is not None:
            device.__exit__(None, None, None)
            if device.overflows:
                logger.warning("Input device overflowed %d times", device.overflows)

    logger.info("Published %d frames in total (%d blocks dropped)", published, dropped)
    if capture_error:
        logger.error("Capture failed: %s", capture_error[0])
        return 1
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    if args.list_devices:
        print(sources.list_devices())
        return 0
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
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    try:
        codec.validate(
            codec.FORMATS[args.format], args.sample_rate, args.channels, args.frame_ms
        )
    except ValueError as exc:
        parser.error(str(exc))

    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )
    session = zenoh.open(conf)
    try:
        with (
            GracefulShutdown() as shutdown,
            declare_liveliness(
                session,
                args.realm,
                args.entity_id,
                args.source_id,
                pubsub_subjects=PUBSUB_SUBJECTS,
            ),
        ):
            return run(session, args, shutdown)
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
