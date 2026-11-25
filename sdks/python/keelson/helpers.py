"""Helper functions for creating and enclosing common Keelson payloads.

Two clocks, kept apart on purpose:

- ``timestamp`` is when the sample is *of*. It goes in the payload, and it is
  what a consumer reads to know when a value held: ``payload.timestamp``.
- ``enclosed_at`` is when the envelope was *made*: ``Envelope.enclosed_at``,
  the transport clock ``uncover`` returns beside ``received_at``. It defaults
  to the wall clock, so forwarding a device's timestamp still leaves an
  envelope time a latency measurement can use.

A publisher whose "now" is not the wall clock -- a simulator, a replay on a
recording's epoch -- encloses at its own time, so it passes the same time to
both: ``enclose_from_float(v, timestamp=t, enclosed_at=t)``. Passing only
``timestamp`` leaves the envelope on the wall clock, which on a recording's
epoch can be months away from the sample.
"""

import time

from . import enclose
from .payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
    TimestampedString,
    TimestampedBytes,
    TimestampedTimestamp,
)
from .payloads.foxglove.LocationFix_pb2 import LocationFix


def enclose_from_bytes(
    value: bytes, timestamp: int = None, enclosed_at: int = None
) -> bytes:
    """Create and enclose a TimestampedBytes payload.

    Args:
        value: The bytes value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.
        enclosed_at: Optional envelope time in nanoseconds. Defaults to the wall
            clock. See the module docstring for the two clocks.

    Returns:
        Serialized envelope containing the TimestampedBytes payload.
    """
    payload = TimestampedBytes()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString(), enclosed_at=enclosed_at)


def enclose_from_integer(
    value: int, timestamp: int = None, enclosed_at: int = None
) -> bytes:
    """Create and enclose a TimestampedInt payload.

    Args:
        value: The integer value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.
        enclosed_at: Optional envelope time in nanoseconds. Defaults to the wall
            clock. See the module docstring for the two clocks.

    Returns:
        Serialized envelope containing the TimestampedInt payload.
    """
    payload = TimestampedInt()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString(), enclosed_at=enclosed_at)


def enclose_from_float(
    value: float, timestamp: int = None, enclosed_at: int = None
) -> bytes:
    """Create and enclose a TimestampedFloat payload.

    Args:
        value: The float value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.
        enclosed_at: Optional envelope time in nanoseconds. Defaults to the wall
            clock. See the module docstring for the two clocks.

    Returns:
        Serialized envelope containing the TimestampedFloat payload.
    """
    payload = TimestampedFloat()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString(), enclosed_at=enclosed_at)


def enclose_from_string(
    value: str, timestamp: int = None, enclosed_at: int = None
) -> bytes:
    """Create and enclose a TimestampedString payload.

    Args:
        value: The string value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.
        enclosed_at: Optional envelope time in nanoseconds. Defaults to the wall
            clock. See the module docstring for the two clocks.

    Returns:
        Serialized envelope containing the TimestampedString payload.
    """
    payload = TimestampedString()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString(), enclosed_at=enclosed_at)


def enclose_from_lon_lat(
    longitude: float,
    latitude: float,
    timestamp: int = None,
    enclosed_at: int = None,
) -> bytes:
    """Create and enclose a LocationFix payload.

    Args:
        longitude: The longitude in degrees.
        latitude: The latitude in degrees.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.
        enclosed_at: Optional envelope time in nanoseconds. Defaults to the wall
            clock. See the module docstring for the two clocks.

    Returns:
        Serialized envelope containing the LocationFix payload.
    """
    payload = LocationFix()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.latitude = latitude
    payload.longitude = longitude

    return enclose(payload.SerializeToString(), enclosed_at=enclosed_at)


def enclose_from_timestamp(
    value: int, timestamp: int = None, enclosed_at: int = None
) -> bytes:
    """Create and enclose a TimestampedTimestamp payload.

    Args:
        value: The timestamp value in nanoseconds to store.
        timestamp: Optional metadata timestamp in nanoseconds. Defaults to current time.
        enclosed_at: Optional envelope time in nanoseconds. Defaults to the wall
            clock. See the module docstring for the two clocks.

    Returns:
        Serialized envelope containing the TimestampedTimestamp payload.
    """
    payload = TimestampedTimestamp()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value.FromNanoseconds(value)

    return enclose(payload.SerializeToString(), enclosed_at=enclosed_at)
