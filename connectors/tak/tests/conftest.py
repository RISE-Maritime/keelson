#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-tak tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader
from unittest.mock import MagicMock

import pytest
import skarv

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))

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
    tak2keelson.PUBLISHERS.clear()
    tak2keelson.TARGET_STALE_AT.clear()
    yield
    tak2keelson.PUBLISHERS.clear()
    tak2keelson.TARGET_STALE_AT.clear()


def create_zenoh_payload(payload_bytes: bytes):
    payload = MagicMock()
    payload.to_bytes = MagicMock(return_value=payload_bytes)
    return payload


@pytest.fixture
def setup_keelson2tak_args():
    args = MagicMock()
    args.cot_uid = "test-uid"
    args.cot_type = "a-f-S-X"
    args.cot_callsign = None
    args.cot_how = "m-g"
    args.cot_stale_seconds = 60.0
    keelson2tak.ARGS = args
    yield args
    keelson2tak.ARGS = None
