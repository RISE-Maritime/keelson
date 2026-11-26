"""Unit tests for screen2keelson: geometry, window selection, encoding, targets.

None of these need a display or a Zenoh session.
"""

import argparse
from types import SimpleNamespace

import cv2
import numpy
import pytest

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


def test_parse_region_ok(screen2keelson):
    assert screen2keelson.parse_region("10,20,300,400") == screen2keelson.Box(
        10, 20, 300, 400
    )


@pytest.mark.parametrize(
    "text", ["1,2,3", "a,b,c,d", "1,2,3,4,5", "0,0,0,10", "-1,0,5,5"]
)
def test_parse_region_rejects(screen2keelson, text):
    with pytest.raises(argparse.ArgumentTypeError):
        screen2keelson.parse_region(text)


def test_apply_crop_offsets_into_absolute(screen2keelson):
    Box = screen2keelson.Box
    assert screen2keelson.apply_crop(
        Box(100, 50, 800, 600), Box(10, 20, 30, 40)
    ) == Box(110, 70, 30, 40)
    assert screen2keelson.apply_crop(Box(1, 2, 3, 4), None) == Box(1, 2, 3, 4)


def test_clamp_box_partial_and_disjoint(screen2keelson):
    Box = screen2keelson.Box
    bounds = Box(0, 0, 1920, 1080)
    # Window hanging off the top-left corner is clipped to the screen.
    assert screen2keelson.clamp_box(Box(-100, -50, 400, 300), bounds) == Box(
        0, 0, 300, 250
    )
    # Fully inside is unchanged.
    assert screen2keelson.clamp_box(Box(10, 10, 20, 20), bounds) == Box(10, 10, 20, 20)
    # Fully outside yields None.
    assert screen2keelson.clamp_box(Box(5000, 0, 10, 10), bounds) is None


def test_clamp_box_negative_virtual_screen(screen2keelson):
    """A monitor left of the primary has negative coordinates in mss."""
    Box = screen2keelson.Box
    bounds = Box(-1920, 0, 3840, 1080)
    assert screen2keelson.clamp_box(Box(-2000, 100, 500, 200), bounds) == Box(
        -1920, 100, 420, 200
    )


# --------------------------------------------------------------------------- #
# Window selection
# --------------------------------------------------------------------------- #


def _windows(mod):
    Box, W = mod.Box, mod.WindowInfo
    return [
        W(id=1, owner="Google Chrome", title="Keelson docs", box=Box(0, 0, 100, 100)),
        W(id=2, owner="Notes", title="", box=Box(10, 10, 50, 50)),
        W(id=3, owner="Google Chrome", title="Foxglove", box=Box(5, 5, 60, 60)),
    ]


def test_select_window_matches_owner_or_title_case_insensitive(screen2keelson):
    windows = _windows(screen2keelson)
    assert screen2keelson.select_window(windows, "notes").id == 2
    assert screen2keelson.select_window(windows, "FOXGLOVE").id == 3


def test_select_window_prefers_frontmost(screen2keelson):
    assert screen2keelson.select_window(_windows(screen2keelson), "chrome").id == 1


def test_select_window_no_match(screen2keelson):
    assert screen2keelson.select_window(_windows(screen2keelson), "excel") is None


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #


def _bgra(height, width):
    img = numpy.zeros((height, width, 4), dtype=numpy.uint8)
    img[..., 2] = 255  # red in BGRA
    img[..., 3] = 255
    return img


def test_encode_frame_produces_decodable_jpeg(screen2keelson):
    payload = screen2keelson.encode_frame(
        _bgra(120, 160),
        timestamp_ns=1_700_000_000_123_456_789,
        quality=90,
        max_width=None,
    )
    assert payload.format == "jpeg"
    assert payload.timestamp.ToNanoseconds() == 1_700_000_000_123_456_789
    assert payload.frame_id == ""

    img = cv2.imdecode(numpy.frombuffer(payload.data, numpy.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (120, 160, 3)
    b, g, r = img.reshape(-1, 3).mean(axis=0)
    assert r > 200 and g < 50 and b < 50


def test_encode_frame_downscales_keeping_aspect(screen2keelson):
    payload = screen2keelson.encode_frame(
        _bgra(1000, 2000), timestamp_ns=1, quality=80, max_width=500
    )
    img = cv2.imdecode(numpy.frombuffer(payload.data, numpy.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (250, 500)


def test_encode_frame_does_not_upscale(screen2keelson):
    payload = screen2keelson.encode_frame(
        _bgra(100, 200), timestamp_ns=1, quality=80, max_width=1920
    )
    img = cv2.imdecode(numpy.frombuffer(payload.data, numpy.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (100, 200)


def test_encode_frame_sets_frame_id(screen2keelson):
    payload = screen2keelson.encode_frame(
        _bgra(10, 10), timestamp_ns=1, quality=80, max_width=None, frame_id="bridge"
    )
    assert payload.frame_id == "bridge"


def test_encode_frame_accepts_mss_style_noncontiguous_input(screen2keelson):
    """numpy.asarray(ScreenShot) can be a strided view; encoding must cope."""
    wide = _bgra(50, 200)
    view = wide[:, ::2]
    payload = screen2keelson.encode_frame(
        view, timestamp_ns=1, quality=80, max_width=None
    )
    img = cv2.imdecode(numpy.frombuffer(payload.data, numpy.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (50, 100)


# --------------------------------------------------------------------------- #
# Capture targets
# --------------------------------------------------------------------------- #


class FakeSct:
    """Stand-in for mss.MSS exposing only ``monitors``."""

    monitors = [
        {"left": 0, "top": 0, "width": 3840, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]


def test_monitor_target_with_region(screen2keelson):
    Box = screen2keelson.Box
    target = screen2keelson.MonitorTarget(FakeSct(), 2, Box(100, 100, 640, 480))
    assert target.resolve() == Box(2020, 100, 640, 480)


def test_monitor_target_region_clipped_to_monitor(screen2keelson):
    Box = screen2keelson.Box
    target = screen2keelson.MonitorTarget(FakeSct(), 1, Box(1800, 1000, 640, 480))
    assert target.resolve() == Box(1800, 1000, 120, 80)


def test_monitor_target_rejects_unknown_index(screen2keelson):
    with pytest.raises(ValueError, match="monitor 5 does not exist"):
        screen2keelson.MonitorTarget(FakeSct(), 5, None)


def test_monitor_target_rejects_region_outside(screen2keelson):
    with pytest.raises(ValueError, match="outside monitor"):
        screen2keelson.MonitorTarget(
            FakeSct(), 1, screen2keelson.Box(5000, 5000, 10, 10)
        )


class FakeLister:
    """Returns a scripted sequence of window lists, one per call."""

    def __init__(self, *frames):
        self.frames = list(frames)

    def __call__(self):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


def test_window_target_follows_moves_and_locks_on_id(screen2keelson):
    Box, W = screen2keelson.Box, screen2keelson.WindowInfo
    first = W(id=7, owner="Notes", title="a", box=Box(10, 10, 100, 100))
    moved = W(id=7, owner="Notes", title="a", box=Box(200, 50, 100, 100))
    # A second "Notes" window comes to the front; we must stay on id 7.
    other = W(id=8, owner="Notes", title="b", box=Box(0, 0, 50, 50))
    lister = FakeLister([first], [other, moved])

    target = screen2keelson.WindowTarget(FakeSct(), "notes", None, None, lister=lister)
    assert target.resolve() == Box(10, 10, 100, 100)
    assert target.resolve() == Box(200, 50, 100, 100)


def test_window_target_missing_then_reappears(screen2keelson):
    Box, W = screen2keelson.Box, screen2keelson.WindowInfo
    win = W(id=3, owner="Chart", title="", box=Box(0, 0, 640, 480))
    lister = FakeLister([], [win])
    target = screen2keelson.WindowTarget(FakeSct(), "chart", None, None, lister=lister)
    assert target.resolve() is None
    assert target.resolve() == Box(0, 0, 640, 480)


def test_window_target_by_id_with_crop_and_clamp(screen2keelson):
    Box, W = screen2keelson.Box, screen2keelson.WindowInfo
    win = W(id=42, owner="", title="Radar", box=Box(3700, 900, 400, 400))
    target = screen2keelson.WindowTarget(
        FakeSct(), None, 42, Box(10, 10, 300, 300), lister=lambda: [win]
    )
    # Crop starts at (3710, 910); virtual screen ends at x=3840, y=1080.
    assert target.resolve() == Box(3710, 910, 130, 170)


def test_window_target_by_id_not_found(screen2keelson):
    target = screen2keelson.WindowTarget(FakeSct(), None, 99, None, lister=lambda: [])
    assert target.resolve() is None


# --------------------------------------------------------------------------- #
# Publish loop
# --------------------------------------------------------------------------- #


class FakeShutdown:
    """Requests shutdown after ``n`` checks; wait() never blocks."""

    def __init__(self, n):
        self.n = n

    def is_requested(self):
        self.n -= 1
        return self.n < 0

    def wait(self, timeout=None):
        return False


class FakeGrabSct(FakeSct):
    def __init__(self):
        self.grabs = []

    def grab(self, monitor):
        self.grabs.append(monitor)
        return _bgra(monitor["height"], monitor["width"])


def test_run_publishes_enveloped_compressed_images(screen2keelson, monkeypatch):
    import keelson
    from keelson.payloads.foxglove.CompressedImage_pb2 import CompressedImage

    published = []
    publisher = SimpleNamespace(put=published.append)
    declared = []

    def fake_declare_publisher(session, key):
        declared.append(key)
        return publisher

    monkeypatch.setattr(screen2keelson, "declare_publisher", fake_declare_publisher)

    args = SimpleNamespace(
        realm="r",
        entity_id="e",
        source_id="screen/0",
        window=None,
        window_id=None,
        monitor=1,
        region=screen2keelson.Box(0, 0, 64, 48),
        fps=1000.0,
        jpeg_quality=80,
        max_width=0,
        frame_id="bridge",
    )
    sct = FakeGrabSct()
    screen2keelson.run(object(), args, FakeShutdown(3), sct)

    assert declared == ["r/@v0/e/pubsub/image_compressed/screen/0"]
    assert len(published) == 3
    assert sct.grabs[0] == {"left": 0, "top": 0, "width": 64, "height": 48}

    _, enclosed_at, payload = keelson.uncover(published[0])
    image = CompressedImage.FromString(payload)
    assert image.format == "jpeg"
    assert image.frame_id == "bridge"
    assert image.timestamp.ToNanoseconds() > 0
    assert enclosed_at >= image.timestamp.ToNanoseconds()
