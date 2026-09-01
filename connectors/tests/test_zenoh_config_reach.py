"""Every connector that opens a Zenoh session must be configurable by file.

The reason this is a test rather than a review habit: `--zenoh-config` exists
so an operator can hand a connector an `access_control` block and *know* it is
in force. `ZENOH_CONFIG` makes that ambient — one environment variable on a
fleet, and every connector is expected to pick it up.

A connector that builds its own `zenoh.Config()` silently opts out of both. It
does not fail, it does not warn; it just runs without the deny policy someone
believed they had applied. That is the one failure mode worth a guard, so the
guard is here: parse every connector entry point and check that the ones
opening a session route through `create_zenoh_config` and pass the argument on.
"""

import ast
import pathlib

import pytest

CONNECTORS = pathlib.Path(__file__).resolve().parents[1]


def _imports_zenoh(path):
    """Selects on the import, not on the spelling of the session call.

    Matching `"zenoh.open("` as a substring would make this guard's own reach
    depend on how a binary happens to write that line: a session opened through
    a helper, or a call reformatted across two lines, drops out of the
    parametrization below and takes its coverage with it. Silently — which is
    the exact failure this file exists to catch, one level up.

    A binary cannot open a session without importing zenoh, so that is what is
    asked. Today the two select the same 28 files; only this one keeps doing so.
    """
    tree = ast.parse(path.read_text())
    return any(
        (isinstance(node, ast.Import) and any(a.name == "zenoh" for a in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "zenoh")
        for node in ast.walk(tree)
    )


BINARIES = sorted(
    path for path in CONNECTORS.glob("*/bin/*.py") if _imports_zenoh(path)
)


def _ids(paths):
    return [f"{p.parents[1].name}/{p.name}" for p in paths]


@pytest.mark.unit
def test_the_scan_found_the_connectors():
    """A glob that silently matches nothing would make every test below pass.

    A ratchet, not a floor. Raise it when connectors are added; never lower it.
    The number matters: the gap this file was written to close was eight
    binaries wide, so a bound loose enough to absorb eight of them going missing
    would not have caught the bug in the first place.
    """
    assert len(BINARIES) >= 28


@pytest.mark.unit
@pytest.mark.parametrize("path", BINARIES, ids=_ids(BINARIES))
def test_session_config_comes_from_create_zenoh_config(path):
    """No hand-rolled `zenoh.Config()` in a connector entry point."""
    tree = ast.parse(path.read_text())

    builds_bare_config = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Config"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "zenoh"
        for node in ast.walk(tree)
    )
    assert not builds_bare_config, (
        f"{path.name} builds zenoh.Config() directly, so --zenoh-config and "
        "ZENOH_CONFIG cannot reach it. Use create_zenoh_config() instead."
    )

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_zenoh_config"
    ]
    assert calls, f"{path.name} opens a Zenoh session without create_zenoh_config()"

    for call in calls:
        passed = {kw.arg for kw in call.keywords}
        assert "zenoh_config" in passed, (
            f"{path.name} calls create_zenoh_config() without zenoh_config=, "
            "so --zenoh-config parses and is then ignored."
        )


@pytest.mark.unit
@pytest.mark.parametrize("path", BINARIES, ids=_ids(BINARIES))
def test_the_flag_is_on_the_parser(path):
    """`args.zenoh_config` only exists because add_common_arguments put it there."""
    tree = ast.parse(path.read_text())

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "add_common_arguments"
        for node in ast.walk(tree)
    ), (
        f"{path.name} does not call add_common_arguments(), so --zenoh-config "
        "is absent from its command line."
    )
