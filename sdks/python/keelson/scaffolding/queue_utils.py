"""Queue monitoring utilities for Keelson applications."""

import logging
import warnings
from queue import Queue

logger = logging.getLogger(__name__)


def check_queue_backpressure(
    queue: Queue,
    warn_threshold: int = 100,
    error_threshold: int = 1000,
    context: str = "worker",
) -> int:
    """Check queue size and log warnings/errors for backpressure.

    Args:
        queue: Queue to check.
        warn_threshold: Size at which to emit warning.
        error_threshold: Size at which to raise RuntimeError.
        context: Context string for error messages.

    Returns:
        Current queue size.

    Raises:
        RuntimeError: If queue size exceeds error_threshold.
    """
    qsize = queue.qsize()
    logger.debug("Approximate queue size: %d", qsize)

    if qsize > error_threshold:
        raise RuntimeError(
            f"{context} cannot keep up with data flow. "
            f"Queue size is {qsize}. Exiting!"
        )
    elif qsize > warn_threshold:
        warnings.warn(f"Queue size is {qsize}", stacklevel=2)

    return qsize
