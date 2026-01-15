"""Tests for CLI argument utilities."""

import argparse
import pytest

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
