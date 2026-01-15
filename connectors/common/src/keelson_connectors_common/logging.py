"""Shared logging configuration for Keelson connectors."""

import logging
import warnings


def setup_logging(
    level: int = logging.INFO,
    format_string: str = "%(asctime)s %(levelname)s %(name)s %(message)s",
    capture_warnings: bool = True,
) -> None:
    """Configure logging for a Keelson connector.

    Args:
        level: The logging level to use.
        format_string: The format string for log messages.
        capture_warnings: Whether to capture Python warnings as log messages.
    """
    logging.basicConfig(format=format_string, level=level)

    if capture_warnings:
        logging.captureWarnings(True)
        warnings.filterwarnings("once")
