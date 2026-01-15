"""Tests for queue monitoring utilities."""

import pytest
from queue import Queue

from keelson_connectors_common.queue_utils import check_queue_backpressure


def test_check_queue_backpressure_returns_queue_size():
    """Test that check_queue_backpressure returns the queue size."""
    queue = Queue()
    for i in range(10):
        queue.put(i)

    size = check_queue_backpressure(queue, warn_threshold=100, error_threshold=1000)
    assert size == 10


def test_check_queue_backpressure_warns_at_threshold():
    """Test that check_queue_backpressure emits warning at threshold."""
    queue = Queue()
    for i in range(150):
        queue.put(i)

    with pytest.warns(UserWarning, match="Queue size is 150"):
        check_queue_backpressure(queue, warn_threshold=100, error_threshold=1000)


def test_check_queue_backpressure_raises_at_error_threshold():
    """Test that check_queue_backpressure raises at error threshold."""
    queue = Queue()
    for i in range(1500):
        queue.put(i)

    with pytest.raises(RuntimeError, match="cannot keep up with data flow"):
        check_queue_backpressure(queue, warn_threshold=100, error_threshold=1000)


def test_check_queue_backpressure_custom_context():
    """Test that check_queue_backpressure uses custom context in error message."""
    queue = Queue()
    for i in range(1500):
        queue.put(i)

    with pytest.raises(RuntimeError, match="recorder cannot keep up"):
        check_queue_backpressure(
            queue, warn_threshold=100, error_threshold=1000, context="recorder"
        )


def test_check_queue_backpressure_empty_queue():
    """Test that check_queue_backpressure works with empty queue."""
    queue = Queue()
    size = check_queue_backpressure(queue, warn_threshold=100, error_threshold=1000)
    assert size == 0
