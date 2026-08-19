#!/usr/bin/env python3
"""Reconstruct claim standings and their justification at an instant, from
a recorded warrant_record stream (an MCAP produced by keelson2mcap) or
from a JSONL debug log."""

import argparse
from pathlib import Path

from warrant_aggregator.records import (
    format_record,
    load_jsonl,
    load_mcap,
    reconstruct,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mcap", type=Path, help="Recording containing warrant_record messages"
    )
    parser.add_argument("--jsonl", type=Path, help="JSONL debug log")
    parser.add_argument(
        "--source-id",
        default=None,
        help="Producing evaluator to reconstruct (MCAP with several producers)",
    )
    parser.add_argument(
        "--at",
        required=True,
        type=float,
        help="Instant to reconstruct: seconds since the first event",
    )
    parser.add_argument(
        "--claims", default=None, help="Comma-separated claim ids (default: all)"
    )
    args = parser.parse_args()

    if bool(args.mcap) == bool(args.jsonl):
        parser.error("exactly one of --mcap and --jsonl is required")
    if args.mcap:
        events = load_mcap(args.mcap, source_id=args.source_id)
    else:
        events = load_jsonl(args.jsonl)
    if not events:
        raise SystemExit("no warrant_record events found")

    first = next(e["t_ns"] for e in events if e["kind"] != "meta")
    state = reconstruct(events, first + int(args.at * 1e9))
    if state["start_ns"] is None:
        state["start_ns"] = first
    claims = args.claims.split(",") if args.claims else None
    print(format_record(state, claims))


if __name__ == "__main__":
    main()
