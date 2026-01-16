#!/usr/bin/env python3

"""Tests for keelson.helpers enclose functions."""

import time
import keelson
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
    TimestampedString,
    TimestampedBytes,
    TimestampedTimestamp,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

# Import helpers from keelson SDK
from keelson.helpers import (
    enclose_from_bytes,
    enclose_from_integer,
    enclose_from_float,
    enclose_from_string,
    enclose_from_lon_lat,
    enclose_from_timestamp,
)


def test_enclose_from_bytes_with_timestamp():
    """Test enclose_from_bytes with explicit timestamp."""
    test_bytes = b"test_data"
    test_timestamp = 1234567890000000000  # nanoseconds

    result = enclose_from_bytes(test_bytes, test_timestamp)

    # Decode the result
    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedBytes()
    payload.ParseFromString(payload_bytes)

    assert payload.value == test_bytes
    assert payload.timestamp.ToNanoseconds() == test_timestamp


def test_enclose_from_bytes_without_timestamp():
    """Test enclose_from_bytes defaults to current time."""
    test_bytes = b"test_data"
    before = time.time_ns()

    result = enclose_from_bytes(test_bytes)

    after = time.time_ns()

    # Decode the result
    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedBytes()
    payload.ParseFromString(payload_bytes)

    assert payload.value == test_bytes
    assert before <= payload.timestamp.ToNanoseconds() <= after


def test_enclose_from_integer_with_timestamp():
    """Test enclose_from_integer with explicit timestamp."""
    test_value = 42
    test_timestamp = 1234567890000000000

    result = enclose_from_integer(test_value, test_timestamp)

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedInt()
    payload.ParseFromString(payload_bytes)

    assert payload.value == test_value
    assert payload.timestamp.ToNanoseconds() == test_timestamp


def test_enclose_from_integer_without_timestamp():
    """Test enclose_from_integer defaults to current time."""
    test_value = 123
    before = time.time_ns()

    result = enclose_from_integer(test_value)

    after = time.time_ns()

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedInt()
    payload.ParseFromString(payload_bytes)

    assert payload.value == test_value
    assert before <= payload.timestamp.ToNanoseconds() <= after


def test_enclose_from_float_with_timestamp():
    """Test enclose_from_float with explicit timestamp."""
    test_value = 123.456
    test_timestamp = 1234567890000000000

    result = enclose_from_float(test_value, test_timestamp)

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedFloat()
    payload.ParseFromString(payload_bytes)

    assert abs(payload.value - test_value) < 0.0001
    assert payload.timestamp.ToNanoseconds() == test_timestamp


def test_enclose_from_float_without_timestamp():
    """Test enclose_from_float defaults to current time."""
    test_value = 98.765
    before = time.time_ns()

    result = enclose_from_float(test_value)

    after = time.time_ns()

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedFloat()
    payload.ParseFromString(payload_bytes)

    assert abs(payload.value - test_value) < 0.0001
    assert before <= payload.timestamp.ToNanoseconds() <= after


def test_enclose_from_string_with_timestamp():
    """Test enclose_from_string with explicit timestamp."""
    test_value = "Hello, World!"
    test_timestamp = 1234567890000000000

    result = enclose_from_string(test_value, test_timestamp)

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedString()
    payload.ParseFromString(payload_bytes)

    assert payload.value == test_value
    assert payload.timestamp.ToNanoseconds() == test_timestamp


def test_enclose_from_string_without_timestamp():
    """Test enclose_from_string defaults to current time."""
    test_value = "Test string"
    before = time.time_ns()

    result = enclose_from_string(test_value)

    after = time.time_ns()

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedString()
    payload.ParseFromString(payload_bytes)

    assert payload.value == test_value
    assert before <= payload.timestamp.ToNanoseconds() <= after


def test_enclose_from_lon_lat_with_timestamp():
    """Test enclose_from_lon_lat with explicit timestamp."""
    test_lon = 11.5167
    test_lat = 48.1173
    test_timestamp = 1234567890000000000

    result = enclose_from_lon_lat(test_lon, test_lat, test_timestamp)

    _, _, payload_bytes = keelson.uncover(result)
    payload = LocationFix()
    payload.ParseFromString(payload_bytes)

    assert abs(payload.longitude - test_lon) < 0.0001
    assert abs(payload.latitude - test_lat) < 0.0001
    assert payload.timestamp.ToNanoseconds() == test_timestamp


def test_enclose_from_lon_lat_without_timestamp():
    """Test enclose_from_lon_lat defaults to current time."""
    test_lon = -122.4194
    test_lat = 37.7749
    before = time.time_ns()

    result = enclose_from_lon_lat(test_lon, test_lat)

    after = time.time_ns()

    _, _, payload_bytes = keelson.uncover(result)
    payload = LocationFix()
    payload.ParseFromString(payload_bytes)

    assert abs(payload.longitude - test_lon) < 0.0001
    assert abs(payload.latitude - test_lat) < 0.0001
    assert before <= payload.timestamp.ToNanoseconds() <= after


def test_enclose_from_lon_lat_order():
    """Test that longitude comes before latitude in function signature."""
    # This test verifies the API contract: longitude FIRST, latitude SECOND
    test_lon = 10.0
    test_lat = 20.0

    result = enclose_from_lon_lat(test_lon, test_lat)

    _, _, payload_bytes = keelson.uncover(result)
    payload = LocationFix()
    payload.ParseFromString(payload_bytes)

    assert abs(payload.longitude - test_lon) < 0.0001
    assert abs(payload.latitude - test_lat) < 0.0001


def test_enclose_from_timestamp_with_timestamp():
    """Test enclose_from_timestamp with explicit metadata timestamp."""
    test_value_ns = 9876543210000000000  # The timestamp value being stored
    test_meta_timestamp = 1234567890000000000  # The metadata timestamp

    result = enclose_from_timestamp(test_value_ns, test_meta_timestamp)

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedTimestamp()
    payload.ParseFromString(payload_bytes)

    assert payload.value.ToNanoseconds() == test_value_ns
    assert payload.timestamp.ToNanoseconds() == test_meta_timestamp


def test_enclose_from_timestamp_without_timestamp():
    """Test enclose_from_timestamp defaults metadata timestamp to current time."""
    test_value_ns = 9876543210000000000
    before = time.time_ns()

    result = enclose_from_timestamp(test_value_ns)

    after = time.time_ns()

    _, _, payload_bytes = keelson.uncover(result)
    payload = TimestampedTimestamp()
    payload.ParseFromString(payload_bytes)

    assert payload.value.ToNanoseconds() == test_value_ns
    assert before <= payload.timestamp.ToNanoseconds() <= after
