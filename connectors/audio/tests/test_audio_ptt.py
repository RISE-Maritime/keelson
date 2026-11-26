"""Unit tests for audio_connector.ptt (keying follows the floor, fail-safe)."""

import pytest

from audio_connector import ptt

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeDevice:
    def __init__(self, fail_key=0, fail_unkey=0):
        self.keyed = False
        self.calls = []
        self.fail_key = fail_key
        self.fail_unkey = fail_unkey
        self.closed = False

    def key(self):
        self.calls.append("key")
        if self.fail_key:
            self.fail_key -= 1
            raise OSError("no radio")
        self.keyed = True

    def unkey(self):
        self.calls.append("unkey")
        if self.fail_unkey:
            self.fail_unkey -= 1
            raise OSError("stuck")
        self.keyed = False

    def close(self):
        self.closed = True
        self.keyed = False


def driver(device, clock, **kw):
    return ptt.PttDriver(device, lead_s=0.15, tail_s=0.3, clock=clock, **kw)


def test_keys_on_hold_and_opens_audio_after_the_lead():
    clock, dev = Clock(), FakeDevice()
    d = driver(dev, clock)
    d.floor_held()
    assert dev.keyed and d.keyed
    assert not d.audio_open()
    clock.t = 0.15
    assert d.audio_open()


def test_unkeys_after_the_tail():
    clock, dev = Clock(), FakeDevice()
    d = driver(dev, clock)
    d.floor_held()
    clock.t = 1.0
    d.floor_free()
    clock.t = 1.29
    d.tick()
    assert dev.keyed
    clock.t = 1.3
    d.tick()
    assert not dev.keyed and not d.audio_open()


def test_a_new_holder_within_the_tail_stays_keyed():
    clock, dev = Clock(), FakeDevice()
    d = driver(dev, clock)
    d.floor_held()
    d.floor_free()
    clock.t = 0.1
    d.floor_held()
    clock.t = 1.0
    d.tick()
    assert dev.keyed and dev.calls == ["key"]


def test_failed_key_plays_no_audio_and_is_retried():
    clock, dev = Clock(), FakeDevice(fail_key=1)
    d = driver(dev, clock)
    d.floor_held()
    assert not d.keyed and not d.audio_open()
    clock.t = 0.5
    d.tick()  # before the retry interval
    assert dev.calls == ["key"]
    clock.t = 1.0
    d.tick()
    assert d.keyed and dev.calls == ["key", "key"]


def test_failed_unkey_stays_keyed_and_is_retried():
    clock, dev = Clock(), FakeDevice(fail_unkey=1)
    d = driver(dev, clock)
    d.floor_held()
    d.floor_free()
    clock.t = 0.3
    d.tick()
    assert d.keyed  # we do not pretend to be off air
    clock.t = 1.3
    d.tick()
    assert not d.keyed and not dev.keyed


def test_close_unkeys_at_once_and_ignores_later_holds():
    clock, dev = Clock(), FakeDevice()
    changes = []
    d = driver(dev, clock, on_change=changes.append)
    d.floor_held()
    d.close()
    assert dev.closed and not dev.keyed and not d.keyed
    assert changes == [True, False]
    d.floor_held()
    assert not dev.keyed
    d.close()  # idempotent


def test_command_ptt_runs_the_commands(tmp_path):
    marker = tmp_path / "keyed"
    dev = ptt.CommandPtt(f"touch {marker}", f"rm -f {marker}")
    dev.key()
    assert marker.exists()
    dev.unkey()
    assert not marker.exists()
    dev.close()


def test_command_ptt_failure_raises(tmp_path):
    dev = ptt.CommandPtt("false", "true")
    with pytest.raises(RuntimeError):
        dev.key()


def test_serial_ptt_rejects_an_unknown_line():
    pytest.importorskip("serial")
    with pytest.raises(ValueError):
        ptt.SerialPtt("/dev/null", "CTS")
