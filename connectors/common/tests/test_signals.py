"""Tests for signal handling utilities."""

import signal
import threading
import time

import pytest

from keelson_connectors_common.signals import GracefulShutdown


class TestGracefulShutdown:
    """Tests for GracefulShutdown context manager."""

    def test_enters_and_exits_context(self):
        """Test basic context manager protocol."""
        with GracefulShutdown() as shutdown:
            assert shutdown is not None
            assert not shutdown.is_requested()

    def test_is_requested_initially_false(self):
        """Test that shutdown is not requested initially."""
        with GracefulShutdown() as shutdown:
            assert not shutdown.is_requested()

    def test_programmatic_request(self):
        """Test that shutdown can be requested programmatically."""
        with GracefulShutdown() as shutdown:
            assert not shutdown.is_requested()
            shutdown.request()
            assert shutdown.is_requested()

    def test_wait_with_timeout(self):
        """Test that wait returns False when timeout expires."""
        with GracefulShutdown() as shutdown:
            start = time.time()
            result = shutdown.wait(timeout=0.1)
            elapsed = time.time() - start

            assert not result  # Timeout expired
            assert elapsed >= 0.1
            assert elapsed < 0.5  # Should not wait much longer

    def test_wait_returns_true_when_requested(self):
        """Test that wait returns True when shutdown is requested."""
        with GracefulShutdown() as shutdown:
            # Request shutdown from another thread
            def request_shutdown():
                time.sleep(0.05)
                shutdown.request()

            thread = threading.Thread(target=request_shutdown)
            thread.start()

            result = shutdown.wait(timeout=1.0)
            thread.join()

            assert result  # Shutdown was requested

    def test_on_shutdown_callback(self):
        """Test that on_shutdown callback is called."""
        callback_called = []

        def callback():
            callback_called.append(True)

        with GracefulShutdown(on_shutdown=callback) as shutdown:
            # Simulate signal by calling the handler directly
            shutdown._handle_signal(signal.SIGINT, None)

            assert len(callback_called) == 1
            assert shutdown.is_requested()

    def test_custom_signals(self):
        """Test that custom signals can be specified."""
        # Just verify it doesn't raise an error
        with GracefulShutdown(signals=[signal.SIGINT]) as shutdown:
            assert not shutdown.is_requested()

    def test_restores_original_handlers(self):
        """Test that original signal handlers are restored."""
        original_handler = signal.getsignal(signal.SIGINT)

        with GracefulShutdown(signals=[signal.SIGINT]):
            pass

        # After exiting context, original handler should be restored
        restored_handler = signal.getsignal(signal.SIGINT)
        assert restored_handler == original_handler

    def test_handles_sigterm(self):
        """Test that SIGTERM is handled by default."""
        with GracefulShutdown() as shutdown:
            # Verify SIGTERM is in the handled signals
            assert signal.SIGTERM in shutdown._signals
            assert signal.SIGINT in shutdown._signals

    def test_wait_without_timeout(self):
        """Test wait with no timeout returns immediately when already requested."""
        with GracefulShutdown() as shutdown:
            shutdown.request()
            result = shutdown.wait(timeout=0.1)
            assert result
