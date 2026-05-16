"""CLI / argparse tests for mavlink2keelson."""

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
