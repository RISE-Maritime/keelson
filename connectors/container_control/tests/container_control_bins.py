"""Run or import this connector's bin/ scripts from tests.

A module of its own, uniquely named, rather than helpers in conftest.py: pytest
runs this repository in the default import mode, so every connector's tests
directory lands on sys.path and a `from conftest import ...` would resolve to
whichever connector's conftest was imported first.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"


def bin_command(name: str, *args: str) -> list[str]:
    """The argv to run a bin script with this interpreter.

    ``sys.executable`` is the workspace interpreter under ``uv run``, with the
    keelson SDK installed; the script's own guarded path insert makes the
    ``container_control`` package importable.
    """
    return [sys.executable, str(BIN / f"{name}.py"), *args]


def load_bin(name: str):
    """Import a bin script as a module (hyphenated file names included)."""
    module_name = name.replace("-", "_")
    loader = SourceFileLoader(module_name, str(BIN / f"{name}.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
