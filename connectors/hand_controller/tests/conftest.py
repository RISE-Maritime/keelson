"""Shared pytest fixtures for keelson-connector-hand-controller tests.

The connector entry point lives at `bin/hc2keelson.py`. We load it via
SourceFileLoader (matching the project convention for `bin/` scripts —
see connectors/CLAUDE.md). The script is self-contained: CLI parsing and
HID protocol helpers are inlined, so no sys.path manipulation is needed
to resolve sibling modules.
"""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"


@pytest.fixture(scope="session")
def hc2keelson():
    src = BIN_ROOT / "hc2keelson.py"
    loader = SourceFileLoader("hc2keelson", str(src))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
