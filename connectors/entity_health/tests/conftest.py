"""Shared fixtures for entity_health connector tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

# Make the entity_health package importable for unit tests.
PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

BIN_ROOT = PKG_ROOT / "bin"


def _load_bin_module():
    path = BIN_ROOT / "entity_health2keelson.py"
    loader = SourceFileLoader("entity_health2keelson", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def entity_health_module():
    """Load the bin/ entry point as an importable module."""
    return _load_bin_module()


@pytest.fixture(autouse=True)
def clear_module_state():
    """Clear module-level state between tests."""
    try:
        mod = _load_bin_module()
    except Exception:
        # Module may fail to import if generated protos are missing;
        # unit tests for the pure evaluator don't need it.
        yield
        return

    mod.PUBLISHERS.clear()
    mod.SUBSCRIBERS.clear()
    mod.LIVELINESS_SUBSCRIBERS.clear()
    mod.EVALUATORS.clear()
    mod.CONFIG.clear()
    mod.SESSION = None
    yield
    mod.PUBLISHERS.clear()
    mod.SUBSCRIBERS.clear()
    mod.LIVELINESS_SUBSCRIBERS.clear()
    mod.EVALUATORS.clear()
    mod.CONFIG.clear()
    mod.SESSION = None
