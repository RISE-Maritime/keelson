"""camera2keelson payload construction."""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "bin" / "camera2keelson.py"
_LOADER = SourceFileLoader("camera2keelson", str(_PATH))
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
camera2keelson = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(camera2keelson)


@pytest.mark.unit
def test_compressed_image_carries_the_ingress_timestamp():
    ts = 1_700_000_000_123_456_789
    payload = camera2keelson._compressed_image(b"\xff\xd8", "jpeg", "cam_front", ts)
    assert payload.timestamp.ToNanoseconds() == ts
    assert (payload.frame_id, payload.format, payload.data) == (
        "cam_front",
        "jpeg",
        b"\xff\xd8",
    )


@pytest.mark.unit
def test_compressed_image_without_a_frame_id():
    payload = camera2keelson._compressed_image(b"x", "png", None, 1)
    assert payload.frame_id == ""
    assert payload.timestamp.ToNanoseconds() == 1
