#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-mavlink tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))

# Import bin/ scripts dynamically — they're standalone executables, not packages.
_path = BIN_ROOT / "mavlink2keelson.py"
_loader = SourceFileLoader("mavlink2keelson", str(_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
mavlink2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mavlink2keelson)


@pytest.fixture(autouse=True)
def clear_module_state():
    """Clear PUBLISHERS dict between tests to prevent cross-test pollution."""
    mavlink2keelson.PUBLISHERS.clear()
    yield
    mavlink2keelson.PUBLISHERS.clear()
