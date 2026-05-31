"""Test fixtures.

The main script lives at `bin/hc2keelson` with no `.py` extension, so we load
it manually via importlib. The `bin/` directory is added to sys.path so the
sibling `terminal_inputs` import inside hc2keelson resolves.
"""

import importlib.machinery
import importlib.util
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))


@pytest.fixture(scope="session")
def hc2keelson():
    src = BIN_DIR / "hc2keelson"
    loader = importlib.machinery.SourceFileLoader("hc2keelson", str(src))
    spec = importlib.util.spec_from_loader("hc2keelson", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
