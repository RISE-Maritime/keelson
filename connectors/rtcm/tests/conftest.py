#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-rtcm tests."""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader
from unittest.mock import Mock

import pytest

BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"


def load_connector_module(module_name: str, path: pathlib.Path):
    """Load a connector bin script as a Python module for unit tests."""
    loader = SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)

    # Register before exec_module so import-time decorators such as dataclass
    # can resolve cls.__module__ through sys.modules, matching normal imports.
    sys.modules[spec.name] = module

    spec.loader.exec_module(module)
    return module


rtcm2keelson = load_connector_module("rtcm2keelson", BIN_ROOT / "rtcm2keelson.py")
keelson2rtcm = load_connector_module("keelson2rtcm", BIN_ROOT / "keelson2rtcm.py")
ntrip_cli = load_connector_module("ntrip_cli", BIN_ROOT / "ntrip-cli.py")
ntrip2keelson = load_connector_module(
    "ntrip2keelson",
    BIN_ROOT / "ntrip2keelson.py",
)


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
    return ntrip_cli.RTCMDistributor()


# Valid RTCM v3 type 1005 frame (station coordinates, 25 bytes).
# Source: pyrtcm documentation. Parses as identity=1005.
RTCM_1005_FRAME = bytes.fromhex("d300133ed000038a58d9493c872f34109d07d6af48205ad7f7")
