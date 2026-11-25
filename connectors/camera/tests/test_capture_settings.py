"""Unit tests for camera2keelson's capture settings (#450)."""

import importlib.util
import logging
from pathlib import Path

import cv2
import pytest

_BIN = Path(__file__).resolve().parents[1] / "bin" / "camera2keelson.py"
_spec = importlib.util.spec_from_file_location("camera2keelson", _BIN)
camera2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(camera2keelson)


class FakeCapture:
    """Records set() calls; get() answers with the mode the 'device' grants."""

    def __init__(self, granted: dict):
        self.granted = dict(granted)
        self.calls = []

    def set(self, prop, value):
        self.calls.append((prop, value))
        return True

    def get(self, prop):
        return self.granted[prop]


def _mode(width, height, fps, fourcc):
    return {
        cv2.CAP_PROP_FRAME_WIDTH: float(width),
        cv2.CAP_PROP_FRAME_HEIGHT: float(height),
        cv2.CAP_PROP_FPS: float(fps),
        cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*fourcc)),
    }


@pytest.mark.unit
def test_nothing_is_set_without_a_request(caplog):
    cap = FakeCapture(_mode(640, 480, 30, "YUYV"))
    with caplog.at_level(logging.INFO, logger="camera2keelson"):
        actual = camera2keelson.configure_capture(cap)
    assert cap.calls == []
    assert actual == {"width": 640, "height": 480, "fps": 30.0, "fourcc": "YUYV"}
    assert "640x480 @ 30 fps YUYV" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.unit
def test_format_is_set_before_size_and_size_before_rate():
    cap = FakeCapture(_mode(1920, 1080, 30, "MJPG"))
    camera2keelson.configure_capture(
        cap, width=1920, height=1080, fps=30, fourcc="MJPG"
    )
    assert [prop for prop, _ in cap.calls] == [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
    ]
    assert cap.calls[0][1] == cv2.VideoWriter_fourcc(*"MJPG")


@pytest.mark.unit
def test_granted_mode_logs_no_warning(caplog):
    cap = FakeCapture(_mode(1920, 1080, 29.97, "MJPG"))
    with caplog.at_level(logging.INFO, logger="camera2keelson"):
        actual = camera2keelson.configure_capture(
            cap, width=1920, height=1080, fps=30, fourcc="MJPG"
        )
    assert actual["width"] == 1920 and actual["height"] == 1080
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.unit
def test_a_refused_mode_warns_and_returns_what_was_granted(caplog):
    # The device falls back to its default instead of the requested mode.
    cap = FakeCapture(_mode(640, 480, 30, "YUYV"))
    with caplog.at_level(logging.INFO, logger="camera2keelson"):
        actual = camera2keelson.configure_capture(
            cap, width=1920, height=1080, fps=60, fourcc="MJPG"
        )
    assert actual == {"width": 640, "height": 480, "fps": 30.0, "fourcc": "YUYV"}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "1920x1080" in message and "640x480" in message
    assert "width, height, fps, fourcc" in message


@pytest.mark.unit
def test_a_source_without_a_fourcc_decodes_empty():
    assert camera2keelson._decode_fourcc(0.0) == ""
    assert camera2keelson._decode_fourcc(cv2.VideoWriter_fourcc(*"MJPG")) == "MJPG"


@pytest.mark.unit
def test_a_bare_number_opens_a_device_index(monkeypatch):
    opened = []
    monkeypatch.setattr(camera2keelson.cv2, "VideoCapture", opened.append)
    camera2keelson.open_capture("0")
    camera2keelson.open_capture("/dev/video0")
    camera2keelson.open_capture("rtsp://10.0.0.1/stream")
    assert opened == [0, "/dev/video0", "rtsp://10.0.0.1/stream"]
