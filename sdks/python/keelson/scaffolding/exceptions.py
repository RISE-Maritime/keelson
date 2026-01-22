"""Exception handling utilities for Keelson applications."""

import logging
from contextlib import contextmanager
from typing import Type

logger = logging.getLogger(__name__)


@contextmanager
def suppress_exception(
    *exceptions: Type[BaseException],
    context: str = "operation",
    log_level: int = logging.ERROR,
    reraise: bool = False,
):
    """Context manager that suppresses and logs exceptions.

    Args:
        *exceptions: Exception types to suppress.
        context: Description of the operation for logging.
        log_level: Logging level for the exception message.
        reraise: If True, re-raise the exception after logging.

    Example:
        with suppress_exception(ValueError, context="message processing"):
            process_message(data)
    """
    try:
        yield
    except exceptions as e:
        logger.log(
            log_level,
            "Exception during %s: %s",
            context,
            e,
            exc_info=True,
        )
        if reraise:
            raise
