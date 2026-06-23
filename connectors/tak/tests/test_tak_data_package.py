#!/usr/bin/env python3
"""Unit tests for TAK data/pref package configuration.

These exercise our wiring of pytak.read_pref_package into the connector's
pytak config section. The package parsing itself (unzip, *.pref, PKCS#12 ->
PEM) is pytak's responsibility and is mocked here.
"""

import types
from unittest.mock import patch

import pytest

from conftest import tak2keelson, keelson2tak

PKG_RESULT = {
    "COT_URL": "tls://tak.example.com:8089",
    "PYTAK_TLS_CLIENT_CERT": "/tmp/dp/cert.pem",
    "PYTAK_TLS_CLIENT_KEY": "/tmp/dp/key.pem",
    "PYTAK_TLS_CLIENT_CAFILE": "/tmp/dp/ca.pem",
}


def _args(**overrides):
    base = dict(
        tak_url=None,
        tak_data_package=None,
        tak_client_cert=None,
        tak_client_key=None,
        tak_ca=None,
        tak_insecure=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize("mod", [tak2keelson, keelson2tak])
def test_data_package_populates_tls_section(mod):
    args = _args(tak_data_package="/path/atak.zip")
    with patch.object(
        mod.pytak, "read_pref_package", return_value=dict(PKG_RESULT)
    ) as m:
        section = mod._build_pytak_section(args)
    m.assert_called_once_with("/path/atak.zip")
    assert section == PKG_RESULT


@pytest.mark.parametrize("mod", [tak2keelson, keelson2tak])
def test_data_package_drops_none_values(mod):
    # A plain-TCP pref package (no TLS material) -> None entries must be dropped,
    # since a ConfigParser section only accepts string values.
    with patch.object(
        mod.pytak,
        "read_pref_package",
        return_value={"COT_URL": "tcp://h:8087", "PYTAK_TLS_CLIENT_CERT": None},
    ):
        section = mod._build_pytak_section(_args(tak_data_package="/p.zip"))
    assert section == {"COT_URL": "tcp://h:8087"}


@pytest.mark.parametrize("mod", [tak2keelson, keelson2tak])
def test_data_package_ignores_explicit_certs(mod):
    args = _args(tak_data_package="/p.zip", tak_client_cert="/x.pem")
    with patch.object(mod.pytak, "read_pref_package", return_value=dict(PKG_RESULT)):
        section = mod._build_pytak_section(args)
    # The package value wins; the explicit flag is not consulted.
    assert section["PYTAK_TLS_CLIENT_CERT"] == PKG_RESULT["PYTAK_TLS_CLIENT_CERT"]


@pytest.mark.parametrize("mod", [tak2keelson, keelson2tak])
def test_insecure_applies_with_data_package(mod):
    args = _args(tak_data_package="/p.zip", tak_insecure=True)
    with patch.object(mod.pytak, "read_pref_package", return_value=dict(PKG_RESULT)):
        section = mod._build_pytak_section(args)
    assert section["PYTAK_TLS_DONT_VERIFY"] == "1"
    assert section["PYTAK_TLS_DONT_CHECK_HOSTNAME"] == "1"


@pytest.mark.parametrize("mod", [tak2keelson, keelson2tak])
def test_explicit_url_with_tls_flags(mod):
    args = _args(
        tak_url="tls://h:8089",
        tak_client_cert="/c.pem",
        tak_client_key="/k.pem",
        tak_ca="/ca.pem",
    )
    section = mod._build_pytak_section(args)
    assert section == {
        "COT_URL": "tls://h:8089",
        "PYTAK_TLS_CLIENT_CERT": "/c.pem",
        "PYTAK_TLS_CLIENT_KEY": "/k.pem",
        "PYTAK_TLS_CLIENT_CAFILE": "/ca.pem",
    }


@pytest.mark.parametrize("mod", [tak2keelson, keelson2tak])
def test_explicit_plain_tcp_has_no_tls_keys(mod):
    section = mod._build_pytak_section(_args(tak_url="tcp://h:8087"))
    assert section == {"COT_URL": "tcp://h:8087"}
