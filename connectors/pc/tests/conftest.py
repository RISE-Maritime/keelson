"""Shared fixtures for pc connector tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

# Make the pc package importable for unit tests.
PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))


@pytest.fixture(scope="session")
def pc2keelson():
    """Load the bin/ entry point as an importable module."""
    path = PKG_ROOT / "bin" / "pc2keelson.py"
    loader = SourceFileLoader("pc2keelson", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
