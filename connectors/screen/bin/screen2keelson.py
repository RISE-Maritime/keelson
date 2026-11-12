#!/usr/bin/env python3

"""
Screen capture connector for Keelson.

Captures a whole monitor or a single application window and publishes the
frames as JPEG ``image_compressed`` (``foxglove.CompressedImage``) on the
Zenoh bus -- the same subject and payload as the camera connector, so the
MCAP recorder, replay and Foxglove handle screen captures exactly like camera
images.

Capture uses ``mss`` (macOS, Linux/X11, Windows). Window mode looks the
window up with the platform's own API (Quartz, X11/EWMH, user32) and grabs
its on-screen rectangle, so anything overlapping the window is captured too.
"""

import sys
import time
import logging
import argparse
import platform
from dataclasses import dataclass

import cv2
import numpy
import zenoh

import keelson
from keelson.payloads.foxglove.CompressedImage_pb2 import CompressedImage
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    setup_logging,
)

SUBJECT = "image_compressed"
PUBSUB_SUBJECTS = [SUBJECT]

STATS_INTERVAL_S = 10.0
WINDOW_RETRY_S = 1.0

logger = logging.getLogger("screen2keelson")


# --------------------------------------------------------------------------- #
# Geometry helpers (pure, unit-tested)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Box:
    """A screen rectangle in mss coordinates (virtual-screen space)."""

    left: int
    top: int
    width: int
    height: int

    def as_monitor(self) -> dict:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def parse_region(text: str) -> Box:
    """argparse type for ``--region x,y,w,h`` (relative to the capture target)."""
    try:
        x, y, w, h = (int(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid region {text!r}: expected four integers x,y,w,h"
        ) from None
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(
            f"invalid region {text!r}: x,y must be >= 0 and w,h must be > 0"
        )
    return Box(x, y, w, h)


def apply_crop(box: Box, crop: Box | None) -> Box:
    """Offset ``crop`` (relative to ``box``) into absolute coordinates."""
    if crop is None:
        return box
    return Box(box.left + crop.left, box.top + crop.top, crop.width, crop.height)


def clamp_box(box: Box, bounds: Box) -> Box | None:
    """Intersect ``box`` with ``bounds``; ``None`` when nothing is visible."""
    left = max(box.left, bounds.left)
    top = max(box.top, bounds.top)
    right = min(box.left + box.width, bounds.left + bounds.width)
    bottom = min(box.top + box.height, bounds.top + bounds.height)
    if right <= left or bottom <= top:
        return None
    return Box(left, top, right - left, bottom - top)


# --------------------------------------------------------------------------- #
# Encoding (pure, unit-tested)
# --------------------------------------------------------------------------- #


def encode_frame(
    bgra: numpy.ndarray,
    timestamp_ns: int,
    quality: int,
    max_width: int | None,
    frame_id: str | None = None,
) -> CompressedImage:
    """Turn an (H, W, 4) BGRA capture into a JPEG ``CompressedImage``."""
    img = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

    height, width = img.shape[:2]
    if max_width and width > max_width:
        scale = max_width / width
        img = cv2.resize(
            img,
            (max_width, max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")

    payload = CompressedImage()
    payload.timestamp.FromNanoseconds(timestamp_ns)
    if frame_id is not None:
        payload.frame_id = frame_id
    payload.format = "jpeg"
    payload.data = buf.tobytes()
    return payload


# --------------------------------------------------------------------------- #
# Window lookup (per platform)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WindowInfo:
    """A top-level window, frontmost first in any list returned below."""

    id: int
    owner: str
    title: str
    box: Box

    @property
    def label(self) -> str:
        if self.owner and self.title:
            return f"{self.owner} - {self.title}"
        return self.owner or self.title


def select_window(windows: list[WindowInfo], query: str) -> WindowInfo | None:
    """Frontmost window whose owner or title contains ``query`` (case-insensitive)."""
    needle = query.casefold()
    for window in windows:
        if needle in window.owner.casefold() or needle in window.title.casefold():
            return window
    return None


def _list_windows_darwin() -> list[WindowInfo]:
    import Quartz  # pyobjc-framework-Quartz, macOS only

    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    windows = []
    for info in infos or []:
        # Layer 0 is ordinary application windows; menubar, dock, overlays
        # live on higher layers.
        if info.get("kCGWindowLayer", 0) != 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        box = Box(
            int(bounds.get("X", 0)),
            int(bounds.get("Y", 0)),
            int(bounds.get("Width", 0)),
            int(bounds.get("Height", 0)),
        )
        if box.width <= 0 or box.height <= 0:
            continue
        windows.append(
            WindowInfo(
                id=int(info["kCGWindowNumber"]),
                owner=str(info.get("kCGWindowOwnerName") or ""),
                # Only populated when the process has Screen Recording permission.
                title=str(info.get("kCGWindowName") or ""),
                box=box,
            )
        )
    return windows  # CGWindowList is ordered front to back


_X_DISPLAY = None


def _list_windows_linux() -> list[WindowInfo]:
    global _X_DISPLAY
    from Xlib import X, display as xdisplay  # python-xlib, Linux only

    if _X_DISPLAY is None:
        _X_DISPLAY = xdisplay.Display()
    d = _X_DISPLAY
    root = d.screen().root

    utf8 = d.intern_atom("UTF8_STRING")
    net_wm_name = d.intern_atom("_NET_WM_NAME")
    prop = root.get_full_property(
        d.intern_atom("_NET_CLIENT_LIST_STACKING"), X.AnyPropertyType
    ) or root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    if prop is None:
        logger.warning("Window manager does not publish _NET_CLIENT_LIST (no EWMH)")
        return []

    windows = []
    # The stacking list is bottom to top; report frontmost first.
    for wid in reversed(list(prop.value)):
        try:
            w = d.create_resource_object("window", wid)
            if w.get_attributes().map_state != X.IsViewable:
                continue
            name_prop = w.get_full_property(net_wm_name, utf8)
            if name_prop is not None:
                title = name_prop.value.decode("utf-8", "replace")
            else:
                title = w.get_wm_name() or ""
                if isinstance(title, bytes):
                    title = title.decode("latin-1", "replace")
            wm_class = w.get_wm_class()
            owner = wm_class[1] if wm_class else ""
            geom = w.get_geometry()
            origin = root.translate_coords(w, 0, 0)
            box = Box(origin.x, origin.y, geom.width, geom.height)
        except Exception:  # window vanished between listing and query
            continue
        if box.width > 0 and box.height > 0:
            windows.append(WindowInfo(id=int(wid), owner=owner, title=title, box=box))
    return windows


def _list_windows_win32() -> list[WindowInfo]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        dwmapi = ctypes.windll.dwmapi
    except OSError:
        dwmapi = None
    DWMWA_EXTENDED_FRAME_BOUNDS = 9

    windows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        # The extended frame bounds exclude the invisible resize border that
        # GetWindowRect includes on Windows 10+.
        if dwmapi is None or dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        ):
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
        box = Box(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        if box.width > 0 and box.height > 0:
            windows.append(WindowInfo(id=int(hwnd), owner="", title=buf.value, box=box))
        return True

    user32.EnumWindows(_callback, 0)  # Z-order, top to bottom
    return windows


def close_window_backend() -> None:
    """Release the cached X11 connection, if one was opened."""
    global _X_DISPLAY
    if _X_DISPLAY is not None:
        _X_DISPLAY.close()
        _X_DISPLAY = None


def list_windows() -> list[WindowInfo]:
    """On-screen top-level windows, frontmost first."""
    system = platform.system()
    if system == "Darwin":
        return _list_windows_darwin()
    if system == "Linux":
        return _list_windows_linux()
    if system == "Windows":
        return _list_windows_win32()
    raise RuntimeError(f"Window capture is not supported on {system}")


def warn_if_no_capture_permission() -> None:
    """macOS silently returns wallpaper-only captures without the permission."""
    if platform.system() != "Darwin":
        return
    try:
        import Quartz

        granted = Quartz.CGPreflightScreenCaptureAccess()
    except Exception:  # very old macOS / pyobjc missing
        return
    if not granted:
        logger.warning(
            "Screen Recording permission is not granted to the app running this "
            "process: captures will only show the desktop and window titles are "
            "unavailable. Grant it in System Settings > Privacy & Security > "
            "Screen Recording, then restart the terminal."
        )


# --------------------------------------------------------------------------- #
# Capture targets
# --------------------------------------------------------------------------- #


def _monitor_box(monitor: dict) -> Box:
    return Box(monitor["left"], monitor["top"], monitor["width"], monitor["height"])


class MonitorTarget:
    """A fixed monitor (index as in mss: 0 = all monitors, 1.. = each one)."""

    def __init__(self, sct, index: int, crop: Box | None):
        monitors = sct.monitors
        if not 0 <= index < len(monitors):
            raise ValueError(
                f"monitor {index} does not exist (available: 0..{len(monitors) - 1})"
            )
        whole = _monitor_box(monitors[index])
        box = clamp_box(apply_crop(whole, crop), whole)
        if box is None:
            raise ValueError(f"region {crop} lies outside monitor {index}")
        self.box = box
        logger.info("Capturing monitor %d: %s", index, box)

    def resolve(self) -> Box | None:
        return self.box


class WindowTarget:
    """A window followed by id; re-selected by query if it goes away."""

    def __init__(
        self,
        sct,
        query: str | None,
        window_id: int | None,
        crop: Box | None,
        lister=list_windows,
    ):
        self._bounds = _monitor_box(sct.monitors[0])
        self._query = query
        self._wanted_id = window_id
        self._crop = crop
        self._lister = lister
        self._current: WindowInfo | None = None
        self._missing_since: float | None = None

    def _find(self) -> WindowInfo | None:
        windows = self._lister()
        locked = self._current.id if self._current else self._wanted_id
        if locked is not None:
            for window in windows:
                if window.id == locked:
                    return window
        if self._query is not None:
            return select_window(windows, self._query)
        return None

    def resolve(self) -> Box | None:
        window = self._find()
        if window is None:
            if self._missing_since is None:
                self._missing_since = time.monotonic()
                logger.warning(
                    "Window %s not found on screen; waiting for it to appear",
                    (
                        repr(self._query)
                        if self._query is not None
                        else f"id={self._wanted_id}"
                    ),
                )
            self._current = None
            return None

        if self._current is None or self._current.id != window.id:
            logger.info("Capturing window %d: %s", window.id, window.label)
        self._current = window
        self._missing_since = None
        return clamp_box(apply_crop(window.box, self._crop), self._bounds)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #


def run(session, args, shutdown: GracefulShutdown, sct) -> None:
    """Capture at ``--fps`` and publish until shutdown is requested."""
    key = keelson.construct_pubsub_key(
        base_path=args.realm,
        entity_id=args.entity_id,
        subject=SUBJECT,
        source_id=args.source_id,
    )
    publisher = declare_publisher(session, key)
    logger.info("Created publisher: %s", key)

    if args.window is not None or args.window_id is not None:
        target = WindowTarget(sct, args.window, args.window_id, args.region)
    else:
        target = MonitorTarget(sct, args.monitor, args.region)

    period = 1.0 / args.fps
    next_tick = time.monotonic()
    stats_start = time.monotonic()
    frames = 0
    bytes_sent = 0

    while not shutdown.is_requested():
        box = target.resolve()
        if box is None:
            shutdown.wait(WINDOW_RETRY_S)
            next_tick = time.monotonic()
            continue

        timestamp_ns = time.time_ns()
        try:
            shot = sct.grab(box.as_monitor())
        except Exception as exc:  # window resized off-screen mid-grab, etc.
            logger.warning("Grab of %s failed: %s", box, exc)
            shutdown.wait(WINDOW_RETRY_S)
            next_tick = time.monotonic()
            continue

        payload = encode_frame(
            numpy.asarray(shot),
            timestamp_ns,
            quality=args.jpeg_quality,
            max_width=args.max_width or None,
            frame_id=args.frame_id,
        )
        publisher.put(keelson.enclose(payload.SerializeToString()))
        frames += 1
        bytes_sent += len(payload.data)

        now = time.monotonic()
        if now - stats_start >= STATS_INTERVAL_S:
            elapsed = now - stats_start
            logger.info(
                "Published %d frames in %.1fs (%.1f fps, avg %.0f kB/frame)",
                frames,
                elapsed,
                frames / elapsed,
                bytes_sent / frames / 1000,
            )
            stats_start, frames, bytes_sent = now, 0, 0

        next_tick += period
        delay = next_tick - time.monotonic()
        if delay > 0:
            shutdown.wait(delay)
        else:
            next_tick = time.monotonic()  # running behind; don't try to catch up


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _open_mss():
    import mss

    return mss.MSS()


def _print_monitors(sct) -> None:
    for index, monitor in enumerate(sct.monitors):
        label = "all monitors" if index == 0 else f"monitor {index}"
        print(
            f"{index}: {monitor['width']}x{monitor['height']} "
            f"at ({monitor['left']},{monitor['top']})  [{label}]"
        )


def _print_windows() -> None:
    for window in list_windows():
        b = window.box
        print(
            f"{window.id}: {window.label!r}  {b.width}x{b.height} at ({b.left},{b.top})"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screen2keelson",
        description=(
            "Capture a monitor or an application window and publish it as JPEG "
            "image_compressed to Keelson/Zenoh"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    add_common_arguments(parser)

    # Required unless only listing targets; enforced in main().
    parser.add_argument(
        "-r",
        "--realm",
        type=str,
        help="Unique id for a domain/realm to connect (e.g. rise) [required]",
    )
    parser.add_argument(
        "-e",
        "--entity-id",
        type=str,
        help="Entity being a unique id representing an entity within the realm "
        "(e.g. landkrabba) [required]",
    )
    parser.add_argument(
        "-s",
        "--source-id",
        type=str,
        help="Source identifier (e.g. screen/0) [required]",
    )

    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="Monitor to capture (0 = all monitors combined, 1.. = single "
        "monitor; see --list-monitors)",
    )
    target.add_argument(
        "-w",
        "--window",
        type=str,
        default=None,
        help="Capture the frontmost window whose app name or title contains this "
        "text (case-insensitive; see --list-windows)",
    )
    target.add_argument(
        "--window-id",
        type=int,
        default=None,
        help="Capture the window with this id (from --list-windows)",
    )

    parser.add_argument(
        "--region",
        type=parse_region,
        default=None,
        metavar="X,Y,W,H",
        help="Crop to this rectangle, relative to the monitor or window",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=5.0,
        help="Capture rate in frames per second",
    )
    parser.add_argument(
        "-q",
        "--jpeg-quality",
        type=int,
        choices=range(1, 101),
        default=80,
        metavar="[1-100]",
        help="JPEG quality",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=1920,
        help="Downscale frames wider than this (keeping aspect ratio); 0 disables",
    )
    parser.add_argument(
        "-f",
        "--frame-id",
        type=str,
        default=None,
        help="Frame ID to include in image payloads",
    )
    parser.add_argument(
        "--list-monitors",
        action="store_true",
        help="List monitors and exit",
    )
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="List on-screen windows and exit",
    )
    return parser


def main():
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or("error")

    if args.list_monitors or args.list_windows:
        warn_if_no_capture_permission()
        try:
            sct = _open_mss()  # also sets DPI awareness on Windows
            with sct:
                if args.list_monitors:
                    _print_monitors(sct)
                if args.list_windows:
                    _print_windows()
        except Exception as exc:
            logger.error("Could not open the screen (no display?): %s", exc)
            sys.exit(1)
        finally:
            close_window_backend()
        return

    missing = [
        flag
        for flag, value in (
            ("-r/--realm", args.realm),
            ("-e/--entity-id", args.entity_id),
            ("-s/--source-id", args.source_id),
        )
        if not value
    ]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    if args.fps <= 0:
        parser.error("--fps must be > 0")
    if args.max_width < 0:
        parser.error("--max-width must be >= 0")

    logger.info("Starting screen2keelson")
    logger.info("Realm: %s", args.realm)
    logger.info("Entity ID: %s", args.entity_id)
    logger.info("Source ID: %s", args.source_id)

    warn_if_no_capture_permission()

    try:
        sct = _open_mss()
    except Exception as exc:
        logger.error("Could not open the screen for capture (no display?): %s", exc)
        sys.exit(1)

    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    logger.info("Opening Zenoh session...")
    session = zenoh.open(conf)
    logger.info("Zenoh session opened")

    try:
        with (
            sct,
            GracefulShutdown() as shutdown,
            declare_liveliness(
                session,
                args.realm,
                args.entity_id,
                args.source_id,
                pubsub_subjects=PUBSUB_SUBJECTS,
            ),
        ):
            run(session, args, shutdown, sct)
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    finally:
        close_window_backend()
        logger.info("Closing Zenoh session...")
        session.close()
        logger.info("Session closed")


if __name__ == "__main__":
    main()
