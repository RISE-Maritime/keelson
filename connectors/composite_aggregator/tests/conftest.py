"""Shared fixtures for composite_aggregator connector tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

# Make the composite_aggregator package importable for unit tests.
PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

BIN_ROOT = PKG_ROOT / "bin"


@pytest.fixture
def composite_aggregator_module():
    """Load the bin/ entry point as an importable module."""
    path = BIN_ROOT / "composite_aggregator2keelson.py"
    loader = SourceFileLoader("composite_aggregator2keelson", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
