"""_forward_sample against a real foxglove sink.

The publisher loop runs _forward_sample under suppress_exception, so a foxglove-sdk signature
change (0.30 dropped Channel.log's publish_time) would drop every message while the server kept
running and the e2e test, which only checks the server is up, stayed green. Here the messages
must actually reach a sink: an MCAP file written by foxglove.open_mcap, read back.
"""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import foxglove
import pytest
from mcap.reader import make_reader

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))

_loader = SourceFileLoader(
    "keelson2foxglove_fwd", str(BIN_ROOT / "keelson2foxglove.py")
)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
keelson2foxglove = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(keelson2foxglove)

KEY = "rise/@v0/boat/pubsub/heading_true_north_deg/gnss"


class _Payload:
    def __init__(self, data: bytes):
        self._data = data

    def to_bytes(self) -> bytes:
        return self._data


class _Sample:
    def __init__(self, key: str, data: bytes):
        self.key_expr = key
        self.payload = _Payload(data)


def _heading(value: float) -> bytes:
    msg = TimestampedFloat(value=value)
    msg.timestamp.GetCurrentTime()
    return msg.SerializeToString()


@pytest.mark.unit
def test_samples_reach_a_real_foxglove_sink(tmp_path):
    out = tmp_path / "out.mcap"
    payloads = [_heading(10.0), _heading(20.0)]
    writer = foxglove.open_mcap(out)
    try:
        channels = {}
        # The first sample creates the Channel; the second takes the known-key path. Both
        # call Channel.log, which is where an SDK signature change would bite.
        for p in payloads:
            keelson2foxglove._forward_sample(channels, _Sample(KEY, keelson.enclose(p)))
        assert list(channels) == [KEY]
    finally:
        writer.close()

    with out.open("rb") as fh:
        reader = make_reader(fh)
        got = [
            (channel.topic, schema.name, message.data, message.log_time)
            for schema, channel, message in reader.iter_messages()
        ]
    assert [g[0] for g in got] == [KEY, KEY]
    assert {g[1] for g in got} == {"keelson.TimestampedFloat"}
    assert [g[2] for g in got] == payloads
    assert all(g[3] > 0 for g in got)


@pytest.mark.unit
def test_unknown_subject_is_skipped_without_a_channel(tmp_path):
    channels = {}
    key = "rise/@v0/boat/pubsub/not_a_known_subject/x"
    keelson2foxglove._forward_sample(channels, _Sample(key, keelson.enclose(b"x")))
    assert channels == {}
