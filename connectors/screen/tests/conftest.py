"""Fixtures for the screen connector tests."""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"


def _load_screen2keelson():
    path = BIN_ROOT / "screen2keelson.py"
    loader = SourceFileLoader("screen2keelson", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def screen2keelson():
    """The bin/screen2keelson.py module, imported without running main()."""
    return _load_screen2keelson()
