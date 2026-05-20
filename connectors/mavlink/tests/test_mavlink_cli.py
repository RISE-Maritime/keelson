"""CLI / argparse tests for mavlink2keelson."""

import argparse
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from conftest import mavlink2keelson


class TestArgParser:
    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            mavlink2keelson.main(["--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "mavlink2keelson" in captured.out
        assert "--mavlink-url" in captured.out

    def test_required_args_enforced(self, capsys):
        # Missing every required arg should fail (exit 2).
        with pytest.raises(SystemExit) as exc:
            mavlink2keelson.build_arg_parser().parse_args([])
        assert exc.value.code == 2

    def test_minimum_required_args(self):
        parser = mavlink2keelson.build_arg_parser()
        args = parser.parse_args(
            [
                "-r",
                "test",
                "-e",
                "ent",
                "-s",
                "mav/0",
                "--mavlink-url",
                "udpin:127.0.0.1:14550",
                "--target-system",
                "1",
            ]
        )
        assert args.realm == "test"
        assert args.entity_id == "ent"
        assert args.source_id == "mav/0"
        assert args.mavlink_url == "udpin:127.0.0.1:14550"
        assert args.target_system == 1
        # Defaults
        assert args.target_component == 0
        assert args.source_system == 254
        assert args.recv_timeout == 1.0


class TestOpenMavlink:
    """We don't actually open a connection here — just verify the kwargs
    routing decision (serial vs network/tlog)."""

    def test_serial_path_passes_baud(self, monkeypatch):
        captured = {}

        def fake_mav(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(mavlink2keelson.mavutil, "mavlink_connection", fake_mav)
        mavlink2keelson.open_mavlink("/dev/ttyUSB0", 254, 191, 57600)
        assert captured["url"] == "/dev/ttyUSB0"
        assert captured["kwargs"]["baud"] == 57600
        assert captured["kwargs"]["source_system"] == 254
        assert captured["kwargs"]["source_component"] == 191

    @pytest.mark.parametrize(
        "url",
        [
            "udpin:0.0.0.0:14550",
            "udpout:127.0.0.1:14550",
            "tcp:localhost:5760",
        ],
    )
    def test_network_url_drops_baud(self, monkeypatch, url):
        captured = {}

        def fake_mav(u, **kwargs):
            captured["url"] = u
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(mavlink2keelson.mavutil, "mavlink_connection", fake_mav)
        mavlink2keelson.open_mavlink(url, 254, 191, 57600)
        assert captured["url"] == url
        assert "baud" not in captured["kwargs"]
        assert captured["kwargs"]["source_system"] == 254

    def test_tlog_prefix_stripped(self, monkeypatch):
        captured = {}

        def fake_mav(u, **kwargs):
            captured["url"] = u
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(mavlink2keelson.mavutil, "mavlink_connection", fake_mav)
        mavlink2keelson.open_mavlink("tlog:/tmp/flight.tlog", 254, 191, 57600)
        assert captured["url"] == "/tmp/flight.tlog"
        assert "baud" not in captured["kwargs"]


class TestDefaultConfigPath:
    """KEELSON_STATE_DIR redirects the channel cache to a mounted volume
    for container deployments; missing env var falls back to ~/.keelson."""

    def test_env_var_overrides_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KEELSON_STATE_DIR", str(tmp_path))
        path = mavlink2keelson._default_config_path("ssrs18")
        assert path == tmp_path / "mavlink-ssrs18.json"

    def test_no_env_var_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("KEELSON_STATE_DIR", raising=False)
        path = mavlink2keelson._default_config_path("ssrs18")
        assert path.parent.name == ".keelson"
        assert path.name == "mavlink-ssrs18.json"

    def test_empty_env_var_falls_back_to_home(self, monkeypatch):
        # Empty string is a common Docker pitfall (ENV KEELSON_STATE_DIR=).
        monkeypatch.setenv("KEELSON_STATE_DIR", "")
        path = mavlink2keelson._default_config_path("ssrs18")
        assert path.parent.name == ".keelson"

    def test_slash_in_entity_id_replaced(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KEELSON_STATE_DIR", str(tmp_path))
        path = mavlink2keelson._default_config_path("fleet/boat-01")
        assert path == tmp_path / "mavlink-fleet_boat-01.json"


class TestResolveChannelsTruthiness:
    """Regression: CLI channel 0 is meaningful (means 'disabled'), and must
    win over both the cache and live-detected values. The previous
    `cli_steering or steering` returned the cached/detected value when the
    CLI said 0."""

    def _args(self, tmp_path, *, steering, throttle, url="udpin:127.0.0.1:14550"):
        return argparse.Namespace(
            steering_channel=steering,
            throttle_channel=throttle,
            mavlink_url=url,
            target_system=1,
            target_component=0,
            config_file=tmp_path / "mavlink-ent.json",
            entity_id="ent",
        )

    def test_cli_zero_wins_over_cache(self, tmp_path, monkeypatch):
        # Seed cache with fingerprint matching what _read_params will return.
        params = {
            "FRAME_CLASS": 1.0,
            "FRAME_TYPE": 0.0,
            "RCMAP_ROLL": 1.0,
            "RCMAP_PITCH": 2.0,
            "RCMAP_THROTTLE": 3.0,
            "RCMAP_YAW": 4.0,
        }
        for i in range(1, 17):
            params[f"SERVO{i}_FUNCTION"] = float(i)
        fingerprint = mavlink2keelson._compute_fingerprint(params)
        cache_file = tmp_path / "mavlink-ent.json"
        cache_file.write_text(
            json.dumps(
                {
                    "entity_id": "ent",
                    "fingerprint": fingerprint,
                    "config": {"steering_channel": 7, "throttle_channel": 8},
                }
            )
        )
        monkeypatch.setattr(
            mavlink2keelson,
            "_read_params",
            lambda *a, **kw: dict(params),
        )

        args = self._args(tmp_path, steering=0, throttle=None)
        mav = MagicMock()
        steering, throttle = mavlink2keelson._resolve_channels(mav, args)
        assert steering == 0, "CLI steering=0 should win over cached 7"
        assert throttle == 8, "CLI throttle=None should fall through to cached 8"

    def test_cli_zero_wins_over_live_detection(self, tmp_path, monkeypatch):
        # No cache file → goes to the live-detect branch.
        params = {
            "FRAME_CLASS": 1.0,
            "RCMAP_ROLL": 2.0,
            "RCMAP_THROTTLE": 4.0,
        }
        monkeypatch.setattr(
            mavlink2keelson,
            "_read_params",
            lambda *a, **kw: dict(params),
        )
        args = self._args(tmp_path, steering=None, throttle=0)
        mav = MagicMock()
        steering, throttle = mavlink2keelson._resolve_channels(mav, args)
        assert steering == 2, "CLI None should pick up detected RCMAP_ROLL=2"
        assert throttle == 0, "CLI throttle=0 should win over detected RCMAP_THROTTLE=4"


class TestLockingMavProxy:
    """The proxy serialises *_send calls across threads so pymavlink's
    sequence/CRC bookkeeping isn't corrupted by the manual-control
    subscriber thread racing the main RPC thread."""

    def test_send_method_calls_are_mutually_exclusive(self):
        # Counter is incremented inside the send body. If the proxy
        # serialises correctly, max_active never exceeds 1.
        active = [0]
        max_active = [0]
        counter_lock = threading.Lock()

        def slow_send(*args, **kwargs):
            with counter_lock:
                active[0] += 1
                if active[0] > max_active[0]:
                    max_active[0] = active[0]
            time.sleep(0.002)  # widen the window for interleaving
            with counter_lock:
                active[0] -= 1

        inner = MagicMock()
        inner.command_long_send = MagicMock(side_effect=slow_send)
        inner.gps_input_send = MagicMock(side_effect=slow_send)

        proxy = mavlink2keelson._LockingMavProxy(inner, threading.Lock())

        def hammer(method_name, n=20):
            method = getattr(proxy, method_name)
            for _ in range(n):
                method(1, 2, 3)

        threads = [
            threading.Thread(target=hammer, args=("command_long_send",)),
            threading.Thread(target=hammer, args=("command_long_send",)),
            threading.Thread(target=hammer, args=("gps_input_send",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert (
            max_active[0] == 1
        ), f"proxy did not serialise sends — observed {max_active[0]} concurrent calls"
        assert inner.command_long_send.call_count == 40
        assert inner.gps_input_send.call_count == 20

    def test_non_send_attributes_pass_through(self):
        inner = MagicMock()
        inner.srcSystem = 254  # a real attribute pymavlink exposes
        inner.parse_buffer = MagicMock(return_value=["parsed"])
        proxy = mavlink2keelson._LockingMavProxy(inner, threading.Lock())

        # Attribute read — no locking, no wrapping.
        assert proxy.srcSystem == 254

        # Non-_send callable — passes through unwrapped (we don't pay
        # lock overhead on the hot recv path).
        result = proxy.parse_buffer(b"data")
        assert result == ["parsed"]
        inner.parse_buffer.assert_called_once_with(b"data")

    def test_setattr_writes_through_to_inner(self):
        # pymavlink mutates fields on `mav.mav` (e.g. target_system); the
        # proxy must forward those writes so internal pymavlink machinery
        # sees them.
        inner = MagicMock()
        proxy = mavlink2keelson._LockingMavProxy(inner, threading.Lock())
        proxy.target_system = 42
        assert inner.target_system == 42

    def test_install_send_lock_swaps_in_proxy(self):
        mav = MagicMock()
        original = mav.mav
        mavlink2keelson._install_send_lock(mav)
        assert isinstance(mav.mav, mavlink2keelson._LockingMavProxy)
        # And the proxy still delegates to the original inner.
        assert mav.mav._inner is original
