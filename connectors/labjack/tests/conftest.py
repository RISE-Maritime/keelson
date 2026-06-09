"""Fixtures for the LabJack connector tests."""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"

# Load the standalone bin script as an importable module for unit testing.
_path = BIN_ROOT / "labjack2keelson.py"
_loader = SourceFileLoader("labjack2keelson", str(_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
labjack2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(labjack2keelson)
