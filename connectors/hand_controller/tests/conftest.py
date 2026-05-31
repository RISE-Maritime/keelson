"""Shared pytest fixtures for keelson-connector-hand-controller tests.

The connector entry point lives at `bin/hc2keelson.py`. We load it via
SourceFileLoader (matching the project convention for `bin/` scripts —
see connectors/CLAUDE.md). The `bin/` directory is added to sys.path so
the sibling `terminal_inputs` / `joystick_proto` imports inside
hc2keelson resolve.
"""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))


@pytest.fixture(scope="session")
def hc2keelson():
    src = BIN_ROOT / "hc2keelson.py"
    loader = SourceFileLoader("hc2keelson", str(src))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
