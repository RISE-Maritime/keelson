"""Push-to-talk for a transmitter behind the sink (VHF, a PA amplifier relay).

Only the vessel-side sink keys PTT; a remote station never does. Keying
follows the floor, with two delays:

- **Lead.** Audio is held back for ``lead_s`` after keying, because a
  transmitter takes a moment to come up and would clip the first word.
- **Tail.** Unkeying waits ``tail_s`` after the floor is released, so the
  audio still in the jitter buffer goes out.

Fail-safe: :meth:`PttDriver.close` unkeys, the sink calls it on every exit
path, and it is also registered with ``atexit``. A serial line is opened
deasserted, and the OS drops it when the process dies.
"""

from __future__ import annotations

import atexit
import logging
import shlex
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class NoPtt:
    """No transmitter: a PA or intercom that is always live."""

    def key(self) -> None:
        pass

    def unkey(self) -> None:
        pass

    def close(self) -> None:
        pass


class SerialPtt:
    """Key by asserting RTS or DTR, the usual radio-interface wiring."""

    def __init__(self, port: str, line: str = "RTS"):
        import serial  # pyserial; only needed for this PTT kind

        line = line.upper()
        if line not in ("RTS", "DTR"):
            raise ValueError("PTT line must be RTS or DTR")
        self._attr = line.lower()
        self._serial = serial.Serial()
        self._serial.port = port
        # Deasserted before open, so opening never keys the radio.
        self._serial.rts = False
        self._serial.dtr = False
        self._serial.open()

    def _set(self, value: bool) -> None:
        setattr(self._serial, self._attr, value)

    def key(self) -> None:
        self._set(True)

    def unkey(self) -> None:
        self._set(False)

    def close(self) -> None:
        try:
            self._set(False)
        finally:
            self._serial.close()


class CommandPtt:
    """Key by running a command: a GPIO tool, a CAT command, a relay script."""

    def __init__(self, key_command: str, unkey_command: str, timeout_s: float = 5.0):
        self._key = shlex.split(key_command)
        self._unkey = shlex.split(unkey_command)
        self._timeout = timeout_s

    def _run(self, argv) -> None:
        result = subprocess.run(argv, timeout=self._timeout, capture_output=True)
        if result.returncode:
            raise RuntimeError(
                f"PTT command {argv[0]} exited {result.returncode}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )

    def key(self) -> None:
        self._run(self._key)

    def unkey(self) -> None:
        self._run(self._unkey)

    def close(self) -> None:
        self.unkey()


class PttDriver:
    """Drives a PTT device from the floor, with lead, tail and fail-safe unkey."""

    RETRY_S = 1.0  # between attempts after a failed key or unkey

    def __init__(
        self,
        device,
        *,
        lead_s: float = 0.15,
        tail_s: float = 0.3,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[bool], None] = lambda keyed: None,
    ):
        self._device = device
        self.lead_s = lead_s
        self.tail_s = tail_s
        self._clock = clock
        self._on_change = on_change
        self._lock = threading.Lock()
        self._want = False  # the floor is held
        self._keyed = False
        self._keyed_at = 0.0
        self._release_at: Optional[float] = None  # unkey due (tail)
        self._retry_at = 0.0
        self._closed = False
        atexit.register(self.close)

    @property
    def keyed(self) -> bool:
        return self._keyed

    def _apply(self, keyed: bool) -> bool:
        try:
            (self._device.key if keyed else self._device.unkey)()
        except Exception:
            logger.exception("PTT %s failed", "key" if keyed else "unkey")
            # A failed key: stay unkeyed, so no audio plays. A failed unkey:
            # the transmitter may still be on air, so stay keyed. Either way
            # tick() tries again.
            self._retry_at = self._clock() + self.RETRY_S
            return False
        self._keyed = keyed
        self._keyed_at = self._clock()
        logger.info("PTT %s", "keyed" if keyed else "unkeyed")
        self._on_change(keyed)
        return True

    def floor_held(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._want = True
            self._release_at = None
            if not self._keyed:
                self._apply(True)

    def floor_free(self) -> None:
        with self._lock:
            self._want = False
            if self._keyed and self._release_at is None:
                self._release_at = self._clock() + self.tail_s

    def tick(self) -> None:
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            if self._want and not self._keyed and now >= self._retry_at:
                self._apply(True)
            elif (
                not self._want
                and self._keyed
                and self._release_at is not None
                and now >= max(self._release_at, self._retry_at)
            ):
                if self._apply(False):
                    self._release_at = None

    def audio_open(self) -> bool:
        """Whether the holder's audio may play yet (keyed for at least the lead)."""
        with self._lock:
            return self._keyed and self._clock() - self._keyed_at >= self.lead_s

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._want = False
            self._release_at = None
            try:
                self._device.close()
            except Exception:
                logger.exception("PTT close failed")
            if self._keyed:
                self._keyed = False
                self._on_change(False)
