#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-nmea tests."""

import io
import pathlib
import socket
import sys
import threading
from unittest.mock import Mock
from datetime import datetime, timezone

import pytest
import keelson
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedInt,
)
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from nmea2000.decoder import NMEA2000Decoder
from nmea2000.encoder import NMEA2000Encoder
from nmea2000.input_formats import N2KFormat
from nmea2000.message import NMEA2000Field, NMEA2000Message

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


class MockGatewayServer:
    """A minimal TCP server speaking CAN-frame ASCII (YDEN-02 RAW mode).

    Simulates a polite gateway: it repeatedly streams an echoed ISO Request
    (source rewritten to ``claimed_address``) followed by the supplied data
    frames, so a connecting client probes ``claimed_address`` and then receives
    a steady message stream. Everything the client sends (its probe and any
    injected frames) is captured and exposed via :meth:`received_messages`.
    """

    def __init__(self, claimed_address=180, data_frames=None):
        self.claimed_address = claimed_address
        self.received = bytearray()
        self._lock = threading.Lock()
        encoder = NMEA2000Encoder()

        def _encode(message):
            parts = encoder.encode(message, output_format=N2KFormat.CAN_FRAME_ASCII)
            return b"".join(
                part if isinstance(part, (bytes, bytearray)) else part.encode()
                for part in parts
            )

        echo = NMEA2000Message(
            PGN=59904,
            id="isoRequest",
            source=claimed_address,
            destination=255,
            priority=6,
            fields=[NMEA2000Field(id="pgn", value=60928, raw_value=60928)],
        )
        self._frames = [_encode(echo)] + [_encode(f) for f in (data_frames or [])]

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()

    def _serve(self):
        self._server.settimeout(10.0)
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        conn.settimeout(0.5)
        with conn:
            while not self._stop.is_set():
                # Capture whatever the client sends (probe + injected frames).
                try:
                    data = conn.recv(4096)
                    if data:
                        with self._lock:
                            self.received.extend(data)
                except socket.timeout:
                    pass
                except OSError:
                    break
                # Stream our canned frames (echo + any data frames).
                try:
                    for frame in self._frames:
                        conn.sendall(frame)
                except OSError:
                    break

    def received_messages(self):
        """Decode the CAN-frame ASCII lines received from the client."""
        decoder = NMEA2000Decoder()
        messages = []
        with self._lock:
            text = bytes(self.received).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                decoded = decoder.decode(line)
            except Exception:
                continue
            if decoded is not None:
                messages.append(decoded)
        return messages

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5.0)
        self._server.close()


@pytest.fixture
def mock_gateway_server():
    """Factory for MockGatewayServer instances, cleaned up after the test."""
    servers = []

    def _factory(claimed_address=180, data_frames=None):
        server = MockGatewayServer(claimed_address, data_frames)
        servers.append(server)
        server.start()
        return server

    yield _factory

    for server in servers:
        server.stop()
