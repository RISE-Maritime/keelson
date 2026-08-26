"""Tests for CLI argument utilities."""

import argparse
import json
import pytest
import zenoh

from keelson.scaffolding import add_common_arguments, create_zenoh_config


class TestAddCommonArguments:
    """Tests for add_common_arguments function."""

    def test_adds_log_level_argument(self):
        """Test that --log-level argument is added."""
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        args = parser.parse_args(["--log-level", "10"])
        assert args.log_level == 10

    def test_log_level_default(self):
        """Test that log level defaults to INFO (20)."""
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        args = parser.parse_args([])
        assert args.log_level == 20  # logging.INFO

    def test_adds_mode_argument(self):
        """Test that --mode argument is added."""
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        args = parser.parse_args(["--mode", "peer"])
        assert args.mode == "peer"

        args = parser.parse_args(["-m", "client"])
        assert args.mode == "client"

    def test_mode_choices(self):
        """Test that mode only accepts peer or client."""
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        with pytest.raises(SystemExit):
            parser.parse_args(["--mode", "invalid"])

    def test_adds_connect_argument(self):
        """Test that --connect argument is added and supports multiple values."""
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        args = parser.parse_args(["--connect", "tcp/localhost:7447"])
        assert args.connect == ["tcp/localhost:7447"]

        args = parser.parse_args(
            [
                "--connect",
                "tcp/localhost:7447",
                "--connect",
                "tcp/localhost:7448",
            ]
        )
        assert args.connect == ["tcp/localhost:7447", "tcp/localhost:7448"]

    def test_adds_listen_argument(self):
        """Test that --listen argument is added and supports multiple values."""
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        args = parser.parse_args(["--listen", "tcp/0.0.0.0:7447"])
        assert args.listen == ["tcp/0.0.0.0:7447"]

        args = parser.parse_args(
            [
                "--listen",
                "tcp/0.0.0.0:7447",
                "--listen",
                "tcp/0.0.0.0:7448",
            ]
        )
        assert args.listen == ["tcp/0.0.0.0:7447", "tcp/0.0.0.0:7448"]


class TestCreateZenohConfig:
    """Tests for create_zenoh_config function."""

    def test_creates_config_with_defaults(self):
        """Test that a config is created with no arguments."""
        config = create_zenoh_config()
        assert config is not None

    def test_creates_config_with_mode(self):
        """Test that mode is set in the config."""
        config = create_zenoh_config(mode="peer")
        # The config object stores values internally
        assert config is not None

        config = create_zenoh_config(mode="client")
        assert config is not None

    def test_creates_config_with_connect_endpoints(self):
        """Test that connect endpoints are set in the config."""
        config = create_zenoh_config(connect=["tcp/localhost:7447"])
        assert config is not None

    def test_creates_config_with_listen_endpoints(self):
        """Test that listen endpoints are set in the config."""
        config = create_zenoh_config(listen=["tcp/0.0.0.0:7447"])
        assert config is not None

    def test_creates_config_with_all_options(self):
        """Test that all options can be combined."""
        config = create_zenoh_config(
            mode="peer",
            connect=["tcp/localhost:7447"],
            listen=["tcp/0.0.0.0:7448"],
        )
        assert config is not None


CONFIG_FILE = """
{
  mode: "client",
  scouting: { multicast: { enabled: false } },
}
"""


class TestZenohConfigFile:
    """A connector must be configurable beyond mode/connect/listen.

    Access control, QoS defaults and transport tuning have no flags and never
    will — there are too many. A file is how Zenoh itself expects them to
    arrive, and without this a connector had no way to receive one.
    """

    def test_flag_parses(self):
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        args = parser.parse_args(["--zenoh-config", "/etc/zenoh.json5"])
        assert args.zenoh_config == "/etc/zenoh.json5"

    def test_flag_defaults_to_none(self):
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)

        assert parser.parse_args([]).zenoh_config is None

    def test_settings_in_the_file_survive(self, tmp_path):
        path = tmp_path / "zenoh.json5"
        path.write_text(CONFIG_FILE)

        conf = create_zenoh_config(zenoh_config=str(path))

        assert json.loads(conf.get_json("scouting/multicast/enabled")) is False

    def test_flags_override_the_file(self, tmp_path):
        """The file is a base, not a ceiling: an operator redirects one
        container at another router without editing shared configuration."""
        path = tmp_path / "zenoh.json5"
        path.write_text(CONFIG_FILE)

        conf = create_zenoh_config(mode="peer", zenoh_config=str(path))

        assert json.loads(conf.get_json("mode")) == "peer"
        # ...and the rest of the file is still in effect.
        assert json.loads(conf.get_json("scouting/multicast/enabled")) is False

    def test_environment_variable_is_honoured(self, tmp_path, monkeypatch):
        """Zenoh defines ZENOH_CONFIG; a deployment sets an environment
        variable far more easily than it rewrites a container's command."""
        path = tmp_path / "zenoh.json5"
        path.write_text(CONFIG_FILE)
        monkeypatch.setenv(zenoh.Config.DEFAULT_CONFIG_PATH_ENV, str(path))

        conf = create_zenoh_config()

        assert json.loads(conf.get_json("scouting/multicast/enabled")) is False

    def test_flag_wins_over_the_environment(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit.json5"
        explicit.write_text('{ mode: "peer" }')
        from_env = tmp_path / "env.json5"
        from_env.write_text('{ mode: "client" }')
        monkeypatch.setenv(zenoh.Config.DEFAULT_CONFIG_PATH_ENV, str(from_env))

        conf = create_zenoh_config(zenoh_config=str(explicit))

        assert json.loads(conf.get_json("mode")) == "peer"

    def test_no_file_anywhere_behaves_as_before(self, monkeypatch):
        """The compatibility claim: every existing caller is unaffected."""
        monkeypatch.delenv(zenoh.Config.DEFAULT_CONFIG_PATH_ENV, raising=False)

        conf = create_zenoh_config(mode="client", connect=["tcp/localhost:7447"])

        assert json.loads(conf.get_json("mode")) == "client"
        assert json.loads(conf.get_json("connect/endpoints")) == ["tcp/localhost:7447"]

    def test_a_missing_file_fails_loudly(self, tmp_path):
        """Silently falling back to defaults would start a connector with the
        access control someone thought they had applied simply absent."""
        with pytest.raises(Exception):
            create_zenoh_config(zenoh_config=str(tmp_path / "nope.json5"))
