"""Signal handling utilities for graceful shutdown."""

import signal
import logging
from threading import Event
from typing import Optional, Callable, List, Dict

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Context manager for handling graceful shutdown and custom signals.

    Handles SIGINT and SIGTERM by default for shutdown, with optional
    support for custom signal callbacks (e.g., SIGHUP for rotation).

    Example - basic shutdown:
        with GracefulShutdown() as shutdown:
            while not shutdown.is_requested():
                # Do work
                time.sleep(1)

    Example - with SIGHUP for rotation:
        rotation_event = Event()

        with GracefulShutdown(
            custom_handlers={signal.SIGHUP: lambda: rotation_event.set()}
        ) as shutdown:
            while not shutdown.is_requested():
                if rotation_event.is_set():
                    rotation_event.clear()
                    perform_rotation()
                time.sleep(1)
    """

    def __init__(
        self,
        signals: Optional[List[signal.Signals]] = None,
        on_shutdown: Optional[Callable[[], None]] = None,
        custom_handlers: Optional[Dict[signal.Signals, Callable[[], None]]] = None,
    ):
        """Initialize the graceful shutdown handler.

        Args:
            signals: List of signals that trigger shutdown. Defaults to SIGINT and SIGTERM.
            on_shutdown: Optional callback to run when shutdown is requested.
            custom_handlers: Dict mapping signals to custom callbacks (e.g., SIGHUP -> rotate).
                           These signals do NOT trigger shutdown.
        """
        self._shutdown_requested = Event()
        self._on_shutdown = on_shutdown
        self._original_handlers: Dict[signal.Signals, Callable] = {}

        if signals is None:
            signals = [signal.SIGINT, signal.SIGTERM]
        self._shutdown_signals = signals
        self._custom_handlers = custom_handlers or {}

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        """Signal handler that sets the shutdown flag."""
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, initiating graceful shutdown...", sig_name)
        self._shutdown_requested.set()

        if self._on_shutdown:
            self._on_shutdown()

    def _make_custom_handler(self, callback: Callable[[], None]) -> Callable:
        """Create a signal handler that calls the custom callback."""

        def handler(signum: int, frame) -> None:
            sig_name = signal.Signals(signum).name
            logger.info("Received %s, calling custom handler...", sig_name)
            callback()

        return handler

    def __enter__(self) -> "GracefulShutdown":
        """Register signal handlers."""
        # Register shutdown signal handlers
        for sig in self._shutdown_signals:
            self._original_handlers[sig] = signal.signal(
                sig, self._handle_shutdown_signal
            )

        # Register custom signal handlers
        for sig, callback in self._custom_handlers.items():
            if hasattr(signal, sig.name):  # Check signal exists on this platform
                self._original_handlers[sig] = signal.signal(
                    sig, self._make_custom_handler(callback)
                )

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
