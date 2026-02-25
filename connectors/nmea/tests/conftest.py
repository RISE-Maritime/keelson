#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-nmea tests."""

import io
import pathlib
import sys
from unittest.mock import Mock
from datetime import datetime, timezone

import pytest
import keelson
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

# Add bin/ to path for imports
BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))


@pytest.fixture
def bin_path():
    """Path to the bin/ directory."""
    return BIN_ROOT


@pytest.fixture
def mock_zenoh_session():
    """Mock Zenoh session that captures published data."""
    session = Mock()
    publisher = Mock()

    # Store published data for assertions
    publisher.published_data = []

    def mock_put(data):
        publisher.published_data.append(data)

    publisher.put = Mock(side_effect=mock_put)
    session.declare_publisher = Mock(return_value=publisher)

    return session


@pytest.fixture
def mock_zenoh_publisher():
    """Mock Zenoh publisher that captures put() calls."""
    publisher = Mock()
    publisher.published_data = []

    def mock_put(data):
        publisher.published_data.append(data)

    publisher.put = Mock(side_effect=mock_put)
    return publisher


@pytest.fixture
def mock_stdout():
    """Mock sys.stdout to capture NMEA output."""
    return io.StringIO()


@pytest.fixture
def sample_location_fix():
    """Create a sample LocationFix protobuf message."""
    location = LocationFix()
    location.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    location.latitude = 48.1173  # ~48°07.038'N
    location.longitude = 11.5167  # ~11°31.000'E
    location.altitude = 545.4
    return location


@pytest.fixture
def sample_timestamped_float():
    """Create a sample TimestampedFloat protobuf message."""
    msg = TimestampedFloat()
    msg.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    msg.value = 123.45
    return msg


@pytest.fixture
def sample_timestamped_int():
    """Create a sample TimestampedInt protobuf message."""
    msg = TimestampedInt()
    msg.timestamp.FromNanoseconds(
        int(
            datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )
    )
    msg.value = 42
    return msg


@pytest.fixture
def sample_location_fix_payload(sample_location_fix):
    """Create encoded LocationFix payload for skarv."""
    return keelson.enclose(sample_location_fix.SerializeToString())


@pytest.fixture
def sample_timestamped_float_payload(sample_timestamped_float):
    """Create encoded TimestampedFloat payload for skarv."""
    return keelson.enclose(sample_timestamped_float.SerializeToString())


@pytest.fixture
def sample_timestamped_int_payload(sample_timestamped_int):
    """Create encoded TimestampedInt payload for skarv."""
    return keelson.enclose(sample_timestamped_int.SerializeToString())


@pytest.fixture(autouse=True)
def clear_skarv():
    """
    Clear skarv vault and caches before and after tests to ensure test isolation.

    This fixture is automatically used for all tests to prevent state leakage
    between tests when multiple test modules are run together.

    Clears:
    - _vault: The main data store
    - _find_matching_subscribers cache
    - _find_matching_middlewares cache
    - _find_matching_triggers cache
    """
    import skarv

    # Clear vault and caches before test
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()

    yield

    # Clear vault and caches after test
    skarv._vault.clear()
    skarv._find_matching_subscribers.cache_clear()
    skarv._find_matching_middlewares.cache_clear()
    skarv._find_matching_triggers.cache_clear()


@pytest.fixture
def mock_args():
    """Mock argparse namespace for keelson2nmea0183 ARGS global."""
    args = Mock()
    args.talker_id = "GP"
    args.realm = "test/realm"
    args.entity_id = "test_entity"
    args.log_level = 30  # WARNING
    return args
