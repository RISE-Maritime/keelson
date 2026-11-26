#!/usr/bin/env python3

"""
Export the audio in MCAP recordings to audio files.

Every recorded `audio_frame` source becomes one or more files: Ogg Opus for
Opus frames (the recorded packets, not re-encoded) or WAV for PCM frames (or
for Opus with --format wav). Short holes are filled with silence so the audio
lines up with the rest of the recording; a hole longer than --split-gap starts
a new file, named after its own start time. Works offline; needs no bus.
"""

import argparse
import datetime
import logging
import sys
from pathlib import Path

from keelson.scaffolding import setup_logging

from audio_connector.export import ExportOptions, audio_channels, export_mcap

logger = logging.getLogger("audio-export")


def parse_time(value: str) -> int:
    """Epoch seconds, or an ISO 8601 time (UTC unless it says otherwise), to ns."""
    try:
        return int(float(value) * 1e9)
    except ValueError:
        pass
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is neither epoch seconds nor an ISO 8601 time"
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1e9)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio-export",
        description="Export audio_frame sources in MCAP recordings to .ogg/.wav files",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "mcap",
        nargs="+",
        type=Path,
        help="MCAP file(s) of one recording, in time order (rotated files in sequence)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Where to write the files",
    )
    parser.add_argument(
        "--format",
        choices=["auto", "ogg", "wav"],
        default="auto",
        help="auto: Ogg Opus for Opus sources, WAV for PCM sources",
    )
    parser.add_argument(
        "-t",
        "--topic",
        help="Only export sources whose MCAP topic matches this regular expression",
    )
    parser.add_argument(
        "--start",
        type=parse_time,
        help="Export from this time (epoch seconds or ISO 8601)",
    )
    parser.add_argument(
        "--end",
        type=parse_time,
        help="Export until this time (epoch seconds or ISO 8601)",
    )
    parser.add_argument(
        "--split-gap",
        type=float,
        default=5.0,
        help="Seconds of missing audio after which a new file is started",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the audio sources in the files and exit",
    )
    parser.add_argument(
        "--log-level",
        type=int,
        default=logging.INFO,
        help="Log level 10=DEBUG, 20=INFO, 30=WARN, 40=ERROR, 50=CRITICAL",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_logging(level=args.log_level)

    for path in args.mcap:
        if not path.is_file():
            logger.error("No such file: %s", path)
            return 1

    if args.list:
        for path in args.mcap:
            for topic in sorted(audio_channels(path)):
                print(f"{path}\t{topic}")
        return 0

    options = ExportOptions(
        output_dir=args.output_dir,
        format=args.format,
        split_gap_s=args.split_gap,
        start_ns=args.start,
        end_ns=args.end,
    )
    try:
        written = export_mcap(args.mcap, options, topic_filter=args.topic)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    if not written:
        logger.warning("No audio_frame sources found")
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
