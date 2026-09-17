"""Target-scoped liveliness (#253): the AIS subset of the N2K surface."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))
_loader = SourceFileLoader("n2k2keelson", str(BIN_ROOT / "n2k2keelson.py"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
n2k2keelson = importlib.util.module_from_spec(_spec)
_loader.exec_module(n2k2keelson)

pytestmark = pytest.mark.unit


def test_target_subjects_are_a_subset_of_the_supported_surface():
    assert set(n2k2keelson.N2K_TARGET_SUBJECTS) <= set(
        n2k2keelson.N2K_SUPPORTED_SUBJECTS
    )


def test_every_supported_subject_gets_exactly_one_plain_token():
    plain, targeted = n2k2keelson.liveliness_subjects(publish_raw=True)
    assert not set(plain) & set(targeted), "a subject in both lists is declared twice"
    assert set(plain) | set(targeted) == set(n2k2keelson.N2K_SUPPORTED_SUBJECTS) | {
        "raw"
    }
    assert "raw" in plain


def test_target_subjects_match_what_the_ais_handlers_publish():
    """Kept in sync by hand, like N2K_SUPPORTED_SUBJECTS — pin it."""
    import inspect

    src = "".join(
        inspect.getsource(fn)
        for fn in (
            n2k2keelson._publish_ais_position,
            n2k2keelson.handle_pgn_129038,
            n2k2keelson.handle_pgn_129039,
            n2k2keelson.handle_pgn_129794,
        )
    )
    for subject in n2k2keelson.N2K_TARGET_SUBJECTS:
        assert f'"{subject}"' in src, f"{subject} is not published by any AIS handler"
