#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-rtcm tests."""

import pathlib
import importlib.util
import socket
import threading
from importlib.machinery import SourceFileLoader
from unittest.mock import Mock

import pytest

# Import bin/ scripts using SourceFileLoader
BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"

_rtcm2keelson_path = BIN_ROOT / "rtcm2keelson.py"
_loader = SourceFileLoader("rtcm2keelson", str(_rtcm2keelson_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
rtcm2keelson = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rtcm2keelson)

_keelson2rtcm_path = BIN_ROOT / "keelson2rtcm.py"
_loader2 = SourceFileLoader("keelson2rtcm", str(_keelson2rtcm_path))
_spec2 = importlib.util.spec_from_loader(_loader2.name, _loader2)
keelson2rtcm = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(keelson2rtcm)


@pytest.fixture
def bin_path():
    """Path to the bin/ directory."""
    return BIN_ROOT


@pytest.fixture
def mock_zenoh_session():
    """Mock Zenoh session that captures published data."""
    session = Mock()
    publisher = Mock()
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
def distributor():
    """Create a fresh RTCMDistributor instance."""
    return keelson2rtcm.RTCMDistributor()


# Valid RTCM v3 type 1005 frame (station coordinates, 25 bytes).
# Source: pyrtcm documentation. Parses as identity=1005.
RTCM_1005_FRAME = bytes.fromhex("d300133ed000038a58d9493c872f34109d07d6af48205ad7f7")


@pytest.fixture
def mock_rtcm_server():
    """TCP server that streams RTCM v3 frames to connected clients.

    Yields (host, port) tuple. Streams a valid RTCM 1005 frame every
    200ms to each connected client until teardown.
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port = server_socket.getsockname()[1]
    server_socket.listen(5)
    server_socket.settimeout(1.0)

    stop_event = threading.Event()

    def serve():
        while not stop_event.is_set():
            try:
                conn, _ = server_socket.accept()
                conn.settimeout(1.0)
                try:
                    while not stop_event.is_set():
                        conn.sendall(RTCM_1005_FRAME)
                        stop_event.wait(0.2)
                except (OSError, BrokenPipeError):
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
            except socket.timeout:
                continue
            except OSError:
                break

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    yield ("127.0.0.1", port)

    stop_event.set()
    server_socket.close()
    thread.join(timeout=5)
