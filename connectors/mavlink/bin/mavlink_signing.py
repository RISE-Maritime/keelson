#!/usr/bin/env python3

"""MAVLink 2 signing for mavlink2keelson and its setup tool (crowsnest-dev#314).

Signing gives each frame between the connector and the autopilot an origin, an integrity check and a
replay guard: a SHA-256 signature over the frame under a 32-byte secret both ends hold, with a
timestamp that must keep rising. The secret is PROVISIONED OUT OF BAND:

- it lives in a file on the vessel computer, 64 hex characters, readable by its owner only (refused
  otherwise, like an ssh key);
- it reaches the autopilot once, at install, over the local USB link, through
  ``mavlink-signing-setup.py``. The running connector never sends it;
- it is never logged, never put on the bus and never kept on ``args``. Errors name the file, never
  its content.

ArduPilot accepts unsigned frames on channel 0 (SERIAL0, the USB port) whatever its key: that is the
install link. The connector therefore belongs on a telemetry port, where a keyed autopilot drops every
unsigned or wrongly signed command.

``SigningMonitor`` turns pymavlink's counters into the ``sensor_status`` mode the connector publishes
on ``sensor_status/{source}/mavlink_signing``; it states what the link is doing and leaves the health
verdict to the entity_health connector, like every other ``sensor_status`` here.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

KEY_BYTES = 32
WINDOW_S = 10.0

# keelson.SensorStatus.OperatingMode, by value, so this module needs no keelson import.
UNKNOWN, RUNNING, STANDBY, DISABLED, ERROR = 0, 1, 2, 3, 5
MODE_NAMES = {
    UNKNOWN: "UNKNOWN",
    RUNNING: "RUNNING",
    STANDBY: "STANDBY",
    DISABLED: "DISABLED",
    ERROR: "ERROR",
}


class SigningError(ValueError):
    """Signing cannot be set up as asked. The message never carries the key."""


class SigningKeyError(SigningError):
    """A key file that cannot be used. The message names the file and never its content."""


def load_key(path) -> bytes:
    """The 32-byte secret in ``path``: exactly 64 hex characters (one trailing newline allowed), in a
    regular file readable by its owner only."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        raise SigningKeyError(f"signing key file {p}: {exc.strerror}") from None
    if not stat.S_ISREG(st.st_mode):
        raise SigningKeyError(f"signing key file {p}: not a regular file")
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SigningKeyError(
            f"signing key file {p}: mode {stat.S_IMODE(st.st_mode):04o} lets others read it; "
            "chmod 600 it (only its owner may)"
        )
    text = p.read_bytes()
    if text.endswith(b"\n"):
        text = text[:-1]
    if len(text) != 2 * KEY_BYTES:
        raise SigningKeyError(
            f"signing key file {p}: expected {2 * KEY_BYTES} hex characters, found {len(text)}"
        )
    try:
        key = bytes.fromhex(text.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise SigningKeyError(f"signing key file {p}: not hexadecimal") from None
    if key == bytes(KEY_BYTES):
        # ArduPilot reads an all-zero key as "signing off": it would key nothing.
        raise SigningKeyError(
            f"signing key file {p}: the all-zero key disables signing"
        )
    return key


def generate_key(path) -> None:
    """A new random key in a new file, mode 0600. Never overwrites: a lost key is a re-install."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(os.urandom(KEY_BYTES).hex() + "\n")


def apply(mav, key: bytes, accept_unsigned: bool, link_id: int) -> None:
    """Sign every frame ``mav`` sends, and verify every frame it receives.

    A callback is installed in BOTH modes: pymavlink counts an unsigned frame only through it, and the
    monitor has to see unsigned traffic to say why nothing verifies. Required mode answers False, so an
    unsigned or badly signed frame is dropped (``reject_count``); accept mode answers True
    (``unsigned_count``). The timestamp is pymavlink's: the wall clock, in 10 µs since 2015-01-01.

    The link must already speak MAVLink 2: pymavlink replaces a 1.0 parser on the first v2 frame,
    and the signing set up on it goes with it."""
    if getattr(mav, "WIRE_PROTOCOL_VERSION", None) != "2.0":
        raise SigningError(
            f"signing needs a MAVLink 2 link; this one speaks {getattr(mav, 'WIRE_PROTOCOL_VERSION', '?')}"
        )
    mav.setup_signing(
        key,
        sign_outgoing=True,
        allow_unsigned_callback=lambda _mav, _msg_id: accept_unsigned,
        link_id=link_id,
    )


@dataclass(frozen=True)
class Counts:
    good: int = 0
    bad: int = 0
    unsigned: int = 0
    rejected: int = 0

    def __sub__(self, other: "Counts") -> "Counts":
        return Counts(
            self.good - other.good,
            self.bad - other.bad,
            self.unsigned - other.unsigned,
            self.rejected - other.rejected,
        )


def counts_of(mav) -> Counts:
    s = mav.mav.signing
    return Counts(s.goodsig_count, s.badsig_count, s.unsigned_count, s.reject_count)


class SigningMonitor:
    """What the link did over the last ``window_s``, as a ``sensor_status`` mode:

    DISABLED  no key: nothing is signed or verified
    ERROR     a bad signature or a rejected frame in the window, or no verified frame in a whole
              window -- a wrong key, an autopilot not keyed yet, or someone else on the port
    STANDBY   accepting unsigned frames (migration) and unsigned frames are arriving
    RUNNING   frames verify, and nothing was refused
    UNKNOWN   nothing heard yet, and less than a window since the first sample: not yet an ERROR
    """

    def __init__(
        self,
        enabled: bool,
        accept_unsigned: bool = False,
        window_s: float = WINDOW_S,
        read: Optional[Callable[[], Counts]] = None,
    ):
        self.enabled = enabled
        self.accept_unsigned = accept_unsigned
        self.window_s = window_s
        self._read = read
        self._history: list[tuple[float, Counts]] = []

    def sample(self, now_s: float, counts: Optional[Counts] = None) -> int:
        """Record the counters at ``now_s`` and return the mode over the window ending there."""
        if not self.enabled:
            return DISABLED
        c = counts if counts is not None else self._read()
        self._history.append((now_s, c))
        while len(self._history) > 1 and self._history[1][0] <= now_s - self.window_s:
            self._history.pop(0)
        return self.mode()

    def window(self) -> Counts:
        if not self._history:
            return Counts()
        return (
            self._history[-1][1] - self._history[0][1]
            if len(self._history) > 1
            else self._history[-1][1]
        )

    def mode(self) -> int:
        if not self.enabled:
            return DISABLED
        w = self.window()
        if w.bad or w.rejected:
            return ERROR
        if self.accept_unsigned and w.unsigned:
            return STANDBY
        if not w.good:
            span = self._history[-1][0] - self._history[0][0] if self._history else 0.0
            return ERROR if span >= self.window_s else UNKNOWN
        return RUNNING

    def summary(self) -> str:
        """Counts only, for the log: never a frame, never the key."""
        w = self.window()
        return (
            f"last {self.window_s:.0f} s: {w.good} verified, {w.bad} bad signature, "
            f"{w.unsigned} unsigned accepted, {w.rejected} rejected"
        )
