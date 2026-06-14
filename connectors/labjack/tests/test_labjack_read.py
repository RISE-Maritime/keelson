"""Unit tests for the hardware read loop, using a fake LJM module.

These exercise the batched-read and reconnect paths without the native LJM
library or any hardware, by monkeypatching the device-open helpers and driving
``run()`` with a controllable shutdown.
"""

import types

import pytest

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat

from conftest import labjack2keelson


class _FakeLJMError(Exception):
    pass


class _FakeLJM:
    """Stand-in for the ``labjack.ljm`` module."""

    LJMError = _FakeLJMError

    def __init__(self, reads):
        # Each element is either a list of values to return, or an exception
        # instance to raise, on successive eReadNames calls.
        self._reads = list(reads)
        self.read_calls = []
        self.closed = 0

    def eReadNames(self, handle, num, names):
        self.read_calls.append((handle, num, list(names)))
        result = self._reads.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self, handle):
        self.closed += 1


class _FakeSession:
    def __init__(self):
        self.puts = []

    def put(self, key, payload, **kwargs):
        self.puts.append((key, payload))


class _FakeShutdown:
    """Context-manager shutdown that reports "requested" after N is_requested()
    calls, so a test can bound the read loop to a fixed number of iterations."""

    def __init__(self, stop_on_call):
        self.calls = 0
        self.stop_on_call = stop_on_call

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def is_requested(self):
        self.calls += 1
        return self.calls >= self.stop_on_call

    def wait(self, timeout=None):
        return False


def _args():
    return types.SimpleNamespace(
        realm="rise",
        entity_id="rov",
        simulate=False,
        device_type="ANY",
        connection_type="ANY",
        identifier="ANY",
    )


def _decode_value(payload_bytes):
    _, _, inner = keelson.uncover(payload_bytes)
    tf = TimestampedFloat()
    tf.ParseFromString(inner)
    return tf.value


def test_batched_read_publishes_all_channels_in_one_call(monkeypatch):
    fake = _FakeLJM(reads=[[1.0, 2.0]])
    monkeypatch.setattr(labjack2keelson, "_open_and_configure", lambda *a: (fake, 7))
    monkeypatch.setattr(
        labjack2keelson, "GracefulShutdown", lambda: _FakeShutdown(stop_on_call=2)
    )

    session = _FakeSession()
    config = {
        "channels": [
            {"ain": "AIN0", "source_id": "a"},  # passthrough
            {"ain": "AIN1", "source_id": "b", "scale": 4.0},  # x4
        ]
    }

    labjack2keelson.run(session, _args(), config)

    # Exactly one read cycle, and all channels read in a SINGLE eReadNames call.
    assert len(fake.read_calls) == 1
    assert fake.read_calls[0][2] == ["AIN0", "AIN1"]

    # One published sample per channel, scaled, in channel order.
    assert len(session.puts) == 2
    assert _decode_value(session.puts[0][1]) == pytest.approx(1.0)
    assert _decode_value(session.puts[1][1]) == pytest.approx(8.0)
    # Device closed once on clean shutdown.
    assert fake.closed == 1


def test_reconnects_on_read_error(monkeypatch):
    # First read raises, second (after reconnect) succeeds.
    fake = _FakeLJM(reads=[_FakeLJMError("usb hiccup"), [3.3]])
    monkeypatch.setattr(labjack2keelson, "_open_and_configure", lambda *a: (fake, 1))
    # Reconnect hands back the same fake device with a fresh handle.
    reconnects = []

    def _fake_reconnect(args, reg_names, reg_values, shutdown):
        reconnects.append(True)
        return fake, 2

    monkeypatch.setattr(labjack2keelson, "_reconnect", _fake_reconnect)
    monkeypatch.setattr(
        labjack2keelson, "GracefulShutdown", lambda: _FakeShutdown(stop_on_call=3)
    )

    session = _FakeSession()
    config = {"channels": [{"ain": "AIN0", "source_id": "a"}]}

    labjack2keelson.run(session, _args(), config)

    # The failed read triggered exactly one reconnect and closed the device.
    assert reconnects == [True]
    assert fake.closed >= 1
    # Only the successful post-reconnect read was published.
    assert len(session.puts) == 1
    assert _decode_value(session.puts[0][1]) == pytest.approx(3.3)


def test_reconnect_aborts_cleanly_on_shutdown(monkeypatch):
    # Read fails, and shutdown is requested before reconnect succeeds.
    fake = _FakeLJM(reads=[_FakeLJMError("gone")])
    monkeypatch.setattr(labjack2keelson, "_open_and_configure", lambda *a: (fake, 1))
    monkeypatch.setattr(labjack2keelson, "_reconnect", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        labjack2keelson, "GracefulShutdown", lambda: _FakeShutdown(stop_on_call=5)
    )

    session = _FakeSession()
    config = {"channels": [{"ain": "AIN0", "source_id": "a"}]}

    # Should return without raising and without publishing anything.
    labjack2keelson.run(session, _args(), config)
    assert session.puts == []
