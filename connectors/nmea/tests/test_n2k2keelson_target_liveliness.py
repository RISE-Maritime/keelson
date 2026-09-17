"""Target-scoped liveliness (#253): the AIS subset of the N2K surface."""

import inspect
import pathlib
import sys

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))
import n2k_handlers  # noqa: E402

pytestmark = pytest.mark.unit


def test_target_subjects_are_a_subset_of_the_supported_surface():
    assert set(n2k_handlers.N2K_TARGET_SUBJECTS) <= set(
        n2k_handlers.N2K_SUPPORTED_SUBJECTS
    )


def test_every_supported_subject_gets_exactly_one_plain_token():
    plain, targeted = n2k_handlers.liveliness_subjects()
    assert not set(plain) & set(targeted), "a subject in both lists is declared twice"
    assert set(plain) | set(targeted) == set(n2k_handlers.N2K_SUPPORTED_SUBJECTS)


def test_target_subjects_match_what_the_ais_handlers_publish():
    """Kept in sync by hand, like N2K_SUPPORTED_SUBJECTS — pin it."""
    src = "".join(
        inspect.getsource(fn)
        for fn in (
            n2k_handlers._publish_ais_position,
            n2k_handlers.handle_pgn_129038,
            n2k_handlers.handle_pgn_129039,
            n2k_handlers.handle_pgn_129794,
        )
    )
    for subject in n2k_handlers.N2K_TARGET_SUBJECTS:
        assert f'"{subject}"' in src, f"{subject} is not published by any AIS handler"
