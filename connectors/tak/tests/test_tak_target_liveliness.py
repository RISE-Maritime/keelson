"""Target-scoped liveliness (#253): CoT subjects are about other entities."""

import argparse

import pytest

from conftest import tak2keelson

pytestmark = pytest.mark.unit


def test_cot_subjects_are_targeted_and_raw_is_not():
    plain, targeted = tak2keelson.liveliness_subjects(
        argparse.Namespace(publish_raw=True)
    )
    assert plain == ["raw"]
    assert targeted == list(tak2keelson.COT_SUPPORTED_SUBJECTS)


def test_no_raw_no_plain_subjects():
    plain, targeted = tak2keelson.liveliness_subjects(
        argparse.Namespace(publish_raw=False)
    )
    assert plain == []
    assert targeted
