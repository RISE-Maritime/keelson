#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-ais tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader
from unittest.mock import MagicMock

import pytest
import skarv

# Add bin/ to path for imports
BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))

# Import connector scripts dynamically (they are not packages)
_ais2keelson_path = BIN_ROOT / "ais2keelson.py"
_loader = SourceFileLoader("ais2keelson", str(_ais2keelson_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
ais2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ais2keelson)

_keelson2ais_path = BIN_ROOT / "keelson2ais.py"
_loader2 = SourceFileLoader("keelson2ais", str(_keelson2ais_path))
_spec2 = importlib.util.spec_from_loader(_loader2.name, _loader2)
keelson2ais = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(keelson2ais)


@pytest.fixture(autouse=True)
def clear_skarv():
    """Clear skarv vault and caches before and after each test."""
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()

    yield

    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()


@pytest.fixture(autouse=True)
def clear_ais2keelson_state():
    """Clear ais2keelson module-level state between tests."""
    ais2keelson.PUBLISHERS.clear()
    ais2keelson.MSG5_DB.clear()
    yield
    ais2keelson.PUBLISHERS.clear()
    ais2keelson.MSG5_DB.clear()


def create_zenoh_payload(payload_bytes: bytes):
    """Wrap bytes in a mock Zenoh payload with to_bytes()."""
    payload = MagicMock()
    payload.to_bytes = MagicMock(return_value=payload_bytes)
    return payload


@pytest.fixture
def setup_keelson2ais_args():
    """Set module-level ARGS for keelson2ais."""
    args = MagicMock()
    args.talker_id = "AIVDO"
    args.radio_channel = "A"
    keelson2ais.ARGS = args
    yield args
    keelson2ais.ARGS = None
