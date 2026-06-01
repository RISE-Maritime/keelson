"""CLI parsing smoke tests for keelson2rorkult.

These do not start a Zenoh session — they just exercise the argparse
surface and the small endpoint / backoff parsers.
"""

import argparse

import pytest


# ---- endpoint parser -----------------------------------------------------


def test_parse_endpoint_happy_path(keelson2rorkult):
    assert keelson2rorkult._parse_endpoint("192.0.2.50:9000") == ("192.0.2.50", 9000)


def test_parse_endpoint_ipv6_uses_last_colon(keelson2rorkult):
    # rpartition splits on the *last* colon — for an IPv6 literal that
    # is the correct port boundary too (the address itself uses ':' as
    # separator but the port is unambiguous after the rightmost).
    host, port = keelson2rorkult._parse_endpoint("fe80::1:9000")
    assert host == "fe80::1"
    assert port == 9000


def test_parse_endpoint_missing_port(keelson2rorkult):
    with pytest.raises(argparse.ArgumentTypeError):
        keelson2rorkult._parse_endpoint("just-a-host")


def test_parse_endpoint_non_integer_port(keelson2rorkult):
    with pytest.raises(argparse.ArgumentTypeError):
        keelson2rorkult._parse_endpoint("host:not-a-number")


# ---- backoff parser ------------------------------------------------------


def test_parse_backoff_happy_path(keelson2rorkult):
    assert keelson2rorkult._parse_backoff("1.0,30.0") == (1.0, 30.0)


def test_parse_backoff_rejects_single_value(keelson2rorkult):
    with pytest.raises(argparse.ArgumentTypeError):
        keelson2rorkult._parse_backoff("5.0")


def test_parse_backoff_rejects_non_numeric(keelson2rorkult):
    with pytest.raises(argparse.ArgumentTypeError):
        keelson2rorkult._parse_backoff("a,b")


def test_parse_backoff_rejects_min_gt_max(keelson2rorkult):
    with pytest.raises(argparse.ArgumentTypeError):
        keelson2rorkult._parse_backoff("10.0,1.0")


def test_parse_backoff_rejects_zero_min(keelson2rorkult):
    with pytest.raises(argparse.ArgumentTypeError):
        keelson2rorkult._parse_backoff("0,1.0")


# ---- argparse surface ----------------------------------------------------


def test_build_arg_parser_help_exits_zero(keelson2rorkult, capsys):
    parser = keelson2rorkult.build_arg_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "keelson2rorkult" in out
    assert "--mcu-endpoint" in out


def test_build_arg_parser_requires_realm_entity_source(keelson2rorkult):
    parser = keelson2rorkult.build_arg_parser()
    with pytest.raises(SystemExit):
        # Missing --realm / --entity-id / --source-id / --mcu-endpoint
        parser.parse_args([])


def test_build_arg_parser_minimal_valid_args(keelson2rorkult):
    parser = keelson2rorkult.build_arg_parser()
    args = parser.parse_args(
        [
            "--realm",
            "rise",
            "--entity-id",
            "boat-01",
            "--source-id",
            "rorkult/0",
            "--mcu-endpoint",
            "127.0.0.1:9000",
        ]
    )
    assert args.realm == "rise"
    assert args.entity_id == "boat-01"
    assert args.source_id == "rorkult/0"
    assert args.mcu_endpoint == "127.0.0.1:9000"
    # Defaults
    assert args.mcu_connect_timeout_s == 5.0
    assert args.mcu_reconnect_backoff_s == "1.0,30.0"


def test_connector_help_via_subprocess(run_connector):
    """Run the bin script as a real subprocess and check --help."""
    result = run_connector("rorkult", "keelson2rorkult", ["--help"], timeout=10.0)
    assert result.returncode == 0
    assert "--mcu-endpoint" in result.stdout
