#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-tak tests."""

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
_tak2keelson_path = BIN_ROOT / "tak2keelson.py"
_loader = SourceFileLoader("tak2keelson", str(_tak2keelson_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
tak2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tak2keelson)

_keelson2tak_path = BIN_ROOT / "keelson2tak.py"
_loader2 = SourceFileLoader("keelson2tak", str(_keelson2tak_path))
_spec2 = importlib.util.spec_from_loader(_loader2.name, _loader2)
keelson2tak = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(keelson2tak)


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
def clear_tak2keelson_state():
    """Clear tak2keelson module-level state between tests."""
    tak2keelson.PUBLISHERS.clear()
    yield
    tak2keelson.PUBLISHERS.clear()


@pytest.fixture(autouse=True)
def reset_keelson2tak_args():
    """Reset keelson2tak global ARGS between tests."""
    original = keelson2tak.ARGS
    yield
    keelson2tak.ARGS = original


def create_zenoh_payload(payload_bytes: bytes):
    """Wrap bytes in a mock Zenoh payload with to_bytes()."""
    payload = MagicMock()
    payload.to_bytes = MagicMock(return_value=payload_bytes)
    return payload


@pytest.fixture
def mock_args():
    """Provide a minimal mock ARGS namespace for keelson2tak."""
    args = MagicMock()
    args.cot_uid = "test-uid"
    args.cot_type = "a-f-S-X"
    args.cot_callsign = "TESTSHIP"
    args.cot_how = "m-g"
    args.cot_stale_seconds = 60.0
    args.realm = "test-realm"
    args.entity_id = "test-entity"
    args.tak_url = "tcp://localhost:8087"
    args.tak_client_cert = None
    args.tak_client_key = None
    args.tak_ca = None
    args.tak_insecure = False
    args.reconnect_delay = 5.0
    args.emit_at_most_every = 1.0
    args.emit_period = 30.0
    keelson2tak.ARGS = args
    return args
