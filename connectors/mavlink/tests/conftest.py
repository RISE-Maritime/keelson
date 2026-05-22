#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-mavlink tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest
import skarv

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))

# Import bin/ scripts dynamically — they're standalone executables, not packages.
# Order matters: injection_config is imported by mavlink2keelson (Phase 4
# wiring), so load + register it under its module name first.
_ic_path = BIN_ROOT / "injection_config.py"
_ic_loader = SourceFileLoader("injection_config", str(_ic_path))
_ic_spec = importlib.util.spec_from_loader(_ic_loader.name, _ic_loader)
injection_config = importlib.util.module_from_spec(_ic_spec)
sys.modules["injection_config"] = injection_config
_ic_spec.loader.exec_module(injection_config)

_path = BIN_ROOT / "mavlink2keelson.py"
_loader = SourceFileLoader("mavlink2keelson", str(_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
mavlink2keelson = importlib.util.module_from_spec(_spec)
sys.modules["mavlink2keelson"] = mavlink2keelson
_spec.loader.exec_module(mavlink2keelson)


@pytest.fixture(autouse=True)
def clear_module_state():
    """Clear PUBLISHERS dict + skarv state between tests to prevent
    cross-test pollution. Skarv keeps a module-level vault + cached
    subscriber/middleware/trigger lookups that survive across tests
    unless explicitly cleared (per connectors/CLAUDE.md)."""
    mavlink2keelson.PUBLISHERS.clear()
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()
    yield
    mavlink2keelson.PUBLISHERS.clear()
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()
