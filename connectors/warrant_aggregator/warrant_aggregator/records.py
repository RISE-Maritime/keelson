"""The record: engine events on the wire and their reconstruction.

The engine's events become WarrantRecord messages on the warrant_record
subject: standing transitions as they happen, and a full snapshot every
snapshot_period_s so the stream is self-contained. Reconstruction replays
the stream to an instant: the latest snapshot at or before it plus the
transitions since. A JSONL sink exists as a debug convenience; the wire
is the record.
"""

import json

from mcap.reader import make_reader

from keelson.payloads.WarrantRecord_pb2 import WarrantRecord

from warrant_aggregator.wire import event_from_warrant_record


class JsonlWriter:
    """Debug sink: append engine events as JSON lines."""

    def __init__(self, path, meta: dict):
        self.file = open(path, "w")
        self.write({"kind": "meta", **meta})

    def write(self, event):
        self.file.write(json.dumps(event) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_mcap(path, source_id=None):
    """Engine-shaped events from recorded warrant_record messages.

    source_id filters to one producing evaluator; None takes the stream as
    recorded (fine when only one evaluator published).
    """
    events = []
    with open(path, "rb") as f:
        reader = make_reader(f)
        for _schema, channel, message in reader.iter_messages(log_time_order=True):
            if "/pubsub/warrant_record/" not in channel.topic:
                continue
            if source_id is not None and not channel.topic.endswith(f"/{source_id}"):
                continue
            record = WarrantRecord()
            record.ParseFromString(message.data)
            events.append(event_from_warrant_record(record))
    return events


def reconstruct(events, t_ns):
    """State of the record at t_ns: {'level': ..., 'claims': {...}}.

    The level is carried by snapshots when the producing evaluator reports
    one (the JSONL debug sink does); reconstruction from the wire alone
    yields claim standings, and the level lives on the evaluator's typed
    subject (operational_authority for the warrant_aggregator).
    """
    state = {"level": None, "claims": {}, "as_of_ns": None, "start_ns": None}
    last_t_ns = None
    for event in events:
        if last_t_ns is not None and event["t_ns"] < last_t_ns:
            # Recording log-time order and payload timestamps can decouple
            # (relays, clock adjustment, concatenated recordings); replaying
            # a misordered stream would silently truncate or misorder, so
            # refuse instead.
            raise ValueError(
                "record stream is not time-ordered: event at "
                f"{event['t_ns']} ns after {last_t_ns} ns"
            )
        last_t_ns = event["t_ns"]
        if event["t_ns"] > t_ns:
            break
        state["as_of_ns"] = event["t_ns"]
        if event["kind"] == "meta":
            state["start_ns"] = event.get("start_ns")
        elif event["kind"] == "snapshot":
            if event.get("level") is not None:
                state["level"] = event["level"]
            state["claims"] = {
                name: dict(claim) for name, claim in event["claims"].items()
            }
        elif event["kind"] == "standing":
            previous = state["claims"].get(event["claim"], {})
            state["claims"][event["claim"]] = {
                **previous,
                "standing": event["to"],
                # A transition is exactly the event that reconverges the two:
                # _transition commits the same target it was called with, so
                # after one they are equal. Carrying the previous snapshot's
                # target across a transition would report a claim whose
                # evidence just withdrew it as held awaiting an upgrade.
                "target": event["to"],
                "since_ns": event["t_ns"],
                "rebuttals_fired": event["rebuttals_fired"],
                "grounds": event["grounds"],
            }
        elif event["kind"] == "level":
            state["level"] = event["to"]
    return state


def format_record(state: dict, claims=None) -> str:
    """The reconstruction as text: each claim's standing with its
    justification, from the stream's own content."""
    names = claims or list(state["claims"])
    start = state.get("start_ns")
    if start is not None and state["as_of_ns"] is not None:
        when = f"t = {(state['as_of_ns'] - start) / 1e9:.1f} s"
    else:
        when = f"as of event at {state['as_of_ns']} ns"
    level = (
        state["level"]
        if state["level"] is not None
        else "(see the evaluator's typed subject)"
    )
    lines = [f"Licensed level: {level}   ({when})", ""]
    for name in names:
        record = state["claims"].get(name)
        if record is None:
            lines.append(f"{name}: no record")
            continue
        statement = record.get("statement", "")
        target = record.get("target")
        # A held claim is published at its old standing. Saying only that
        # would read as evidence that does not support an upgrade.
        held = f" -> {target} held" if target and target != record["standing"] else ""
        lines.append(
            f"[{record['standing']}{held}] {name}"
            + (f": {statement}" if statement else "")
        )
        if record.get("warrant"):
            lines.append(f"  warrant: {record['warrant']}")
        if record.get("backing"):
            lines.append(f"  backing: {record['backing']}")
        for ground, standing in record.get("grounds", {}).items():
            lines.append(f"  ground: {ground} [{standing}]")
        for rebuttal in record.get("rebuttals_fired", []):
            lines.append(
                f"  rebuttal fired: {rebuttal['id']}, {rebuttal['description']}"
            )
            lines.append(f"    evidence: {rebuttal['evidence']}")
        lines.append("")
    return "\n".join(lines)
