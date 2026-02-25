"""Helper functions for creating and enclosing common Keelson payloads."""

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


def enclose_from_bytes(value: bytes, timestamp: int = None) -> bytes:
    """Create and enclose a TimestampedBytes payload.

    Args:
        value: The bytes value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.

    Returns:
        Serialized envelope containing the TimestampedBytes payload.
    """
    payload = TimestampedBytes()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString())


def enclose_from_integer(value: int, timestamp: int = None) -> bytes:
    """Create and enclose a TimestampedInt payload.

    Args:
        value: The integer value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.

    Returns:
        Serialized envelope containing the TimestampedInt payload.
    """
    payload = TimestampedInt()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString())


def enclose_from_float(value: float, timestamp: int = None) -> bytes:
    """Create and enclose a TimestampedFloat payload.

    Args:
        value: The float value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.

    Returns:
        Serialized envelope containing the TimestampedFloat payload.
    """
    payload = TimestampedFloat()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString())


def enclose_from_string(value: str, timestamp: int = None) -> bytes:
    """Create and enclose a TimestampedString payload.

    Args:
        value: The string value to wrap.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.

    Returns:
        Serialized envelope containing the TimestampedString payload.
    """
    payload = TimestampedString()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value = value

    return enclose(payload.SerializeToString())


def enclose_from_lon_lat(
    longitude: float, latitude: float, timestamp: int = None
) -> bytes:
    """Create and enclose a LocationFix payload.

    Args:
        longitude: The longitude in degrees.
        latitude: The latitude in degrees.
        timestamp: Optional timestamp in nanoseconds. Defaults to current time.

    Returns:
        Serialized envelope containing the LocationFix payload.
    """
    payload = LocationFix()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.latitude = latitude
    payload.longitude = longitude

    return enclose(payload.SerializeToString())


def enclose_from_timestamp(value: int, timestamp: int = None) -> bytes:
    """Create and enclose a TimestampedTimestamp payload.

    Args:
        value: The timestamp value in nanoseconds to store.
        timestamp: Optional metadata timestamp in nanoseconds. Defaults to current time.

    Returns:
        Serialized envelope containing the TimestampedTimestamp payload.
    """
    payload = TimestampedTimestamp()
    payload.timestamp.FromNanoseconds(timestamp or time.time_ns())
    payload.value.FromNanoseconds(value)

    return enclose(payload.SerializeToString())
