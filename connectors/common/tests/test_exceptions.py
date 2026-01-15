"""Tests for exception handling utilities."""

import logging
import pytest

from keelson_connectors_common.exceptions import suppress_exception


def test_suppress_exception_catches_specified_exception():
    """Test that suppress_exception catches specified exceptions."""
    result = []
    with suppress_exception(ValueError, context="test operation"):
        result.append("before")
        raise ValueError("test error")
        result.append("after")  # Should not be reached

    # Code after context manager should still run
    result.append("done")
    assert result == ["before", "done"]


def test_suppress_exception_does_not_catch_unspecified_exception():
    """Test that suppress_exception does not catch unspecified exceptions."""
    with pytest.raises(TypeError):
        with suppress_exception(ValueError, context="test operation"):
            raise TypeError("test error")


def test_suppress_exception_multiple_exceptions():
    """Test that suppress_exception can catch multiple exception types."""
    for exc_type in [ValueError, TypeError, RuntimeError]:
        result = []
        with suppress_exception(ValueError, TypeError, RuntimeError, context="test"):
            result.append("before")
            raise exc_type("test error")

        result.append("done")
        assert result == ["before", "done"]


def test_suppress_exception_reraise():
    """Test that suppress_exception can re-raise exceptions after logging."""
    with pytest.raises(ValueError):
        with suppress_exception(ValueError, context="test", reraise=True):
            raise ValueError("test error")


def test_suppress_exception_logs_with_correct_level(caplog):
    """Test that suppress_exception logs at the correct level."""
    with caplog.at_level(logging.WARNING):
        with suppress_exception(
            ValueError, context="test op", log_level=logging.WARNING
        ):
            raise ValueError("test error")

    assert "Exception during test op" in caplog.text
    assert "test error" in caplog.text


def test_suppress_exception_no_exception():
    """Test that suppress_exception works when no exception is raised."""
    result = []
    with suppress_exception(ValueError, context="test"):
        result.append("executed")

    assert result == ["executed"]
