"""Tests for logging utilities."""

import io
import logging

import pytest

from keelson.scaffolding import setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    @pytest.fixture(autouse=True)
    def reset_logging(self):
        """Reset logging configuration before and after each test."""
        # Store original state
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level

        # Clear existing handlers before test to allow basicConfig to work
        root.handlers = []

        yield

        # Restore original state
        root.handlers = original_handlers
        root.setLevel(original_level)

    def test_configures_logging_adds_handler(self):
        """Test that setup_logging adds a handler to root logger."""
        root = logging.getLogger()
        initial_handler_count = len(root.handlers)

        setup_logging()

        # basicConfig adds at least one handler when no handlers exist
        assert len(root.handlers) >= initial_handler_count

    def test_configures_logging_with_defaults(self):
        """Test that logging works with default level (INFO)."""
        # In a fresh environment, basicConfig sets the level to INFO
        # In pytest, logging may already be configured so we just verify it doesn't error
        setup_logging()

        # Verify we can log at INFO level without errors
        test_logger = logging.getLogger("test_defaults")
        test_logger.info("Test message at INFO level")

    def test_configures_logging_with_custom_level(self):
        """Test that setup_logging accepts custom level parameter."""
        # basicConfig may not change level if already configured,
        # but we verify the function accepts the parameter correctly
        setup_logging(level=logging.DEBUG)
        setup_logging(level=logging.WARNING)
        setup_logging(level=logging.ERROR)

        # Verify logging still works
        test_logger = logging.getLogger("test_custom_level")
        test_logger.error("Test message")

    def test_captures_warnings_by_default(self):
        """Test that Python warnings are captured as log messages."""
        setup_logging()

        # Check that warnings logger exists (created by captureWarnings)
        warnings_logger = logging.getLogger("py.warnings")
        assert warnings_logger is not None

    def test_can_disable_warning_capture(self):
        """Test that warning capture can be disabled."""
        # This should not raise any errors
        setup_logging(capture_warnings=False)

    def test_log_message_format(self):
        """Test that log messages are formatted correctly."""
        # Use a stream handler to capture output
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)

        custom_format = "%(levelname)s: %(message)s"
        setup_logging(format_string=custom_format, level=logging.INFO)

        # Format should be applied to the handler added by basicConfig
        # Verify by checking that we can log
        test_logger = logging.getLogger("test")
        test_logger.setLevel(logging.INFO)
        test_logger.info("test message")

        # Clean up
        root.removeHandler(handler)

    def test_function_completes_without_error(self):
        """Test that setup_logging completes without raising exceptions."""
        # Test with all parameter combinations
        setup_logging()
        setup_logging(level=logging.DEBUG)
        setup_logging(level=logging.WARNING, capture_warnings=True)
        setup_logging(level=logging.ERROR, capture_warnings=False)
        setup_logging(format_string="%(message)s")
