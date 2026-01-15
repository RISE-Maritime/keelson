"""Signal handling utilities for graceful shutdown."""

import signal
import logging
from threading import Event
from typing import Optional, Callable, List

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Context manager for handling graceful shutdown on signals.

    Handles SIGINT and SIGTERM by default, with optional SIGHUP support.

    Example:
        with GracefulShutdown() as shutdown:
            while not shutdown.is_requested():
                # Do work
                time.sleep(1)
    """

    def __init__(
        self,
        signals: Optional[List[signal.Signals]] = None,
        on_shutdown: Optional[Callable[[], None]] = None,
    ):
        """Initialize the graceful shutdown handler.

        Args:
            signals: List of signals to handle. Defaults to SIGINT and SIGTERM.
            on_shutdown: Optional callback to run when shutdown is requested.
        """
        self._shutdown_requested = Event()
        self._on_shutdown = on_shutdown
        self._original_handlers = {}

        if signals is None:
            signals = [signal.SIGINT, signal.SIGTERM]
        self._signals = signals

    def _handle_signal(self, signum: int, frame) -> None:
        """Signal handler that sets the shutdown flag."""
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, initiating graceful shutdown...", sig_name)
        self._shutdown_requested.set()

        if self._on_shutdown:
            self._on_shutdown()

    def __enter__(self) -> "GracefulShutdown":
        """Register signal handlers."""
        for sig in self._signals:
            self._original_handlers[sig] = signal.signal(sig, self._handle_signal)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Restore original signal handlers."""
        for sig, handler in self._original_handlers.items():
            signal.signal(sig, handler)
        return False

    def is_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_requested.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for shutdown to be requested.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if shutdown was requested, False if timeout expired.
        """
        return self._shutdown_requested.wait(timeout)

    def request(self) -> None:
        """Programmatically request shutdown."""
        self._shutdown_requested.set()
