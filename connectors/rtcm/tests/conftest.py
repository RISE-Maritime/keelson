#!/usr/bin/env python3

"""Shared pytest fixtures for keelson-connector-rtcm tests."""

import pathlib
import importlib.util
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
