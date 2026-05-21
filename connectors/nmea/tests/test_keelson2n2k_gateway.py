#!/usr/bin/env python3

"""Tests for keelson2n2k gateway mode (direct CAN-gateway injection)."""

import time
from unittest.mock import Mock

import pytest
from nmea2000.message import NMEA2000Field, NMEA2000Message

# bin/ is on sys.path via the connector conftest.
import keelson2n2k
import n2k_gateway


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module globals so this module's skarv triggers stay inert."""
    keelson2n2k.ARGS = None
    keelson2n2k.RUNNER = None
    yield
    keelson2n2k.ARGS = None
    keelson2n2k.RUNNER = None


@pytest.fixture
def configured():
    """Set ARGS so build_nmea2000_message produces messages."""
    keelson2n2k.ARGS = Mock()
    keelson2n2k.ARGS.source_address = 1
    keelson2n2k.ARGS.priority = 2


def _position_fields():
    return [
        NMEA2000Field(id="latitude", value=59.5, unit_of_measurement="deg"),
        NMEA2000Field(id="longitude", value=18.25, unit_of_measurement="deg"),
    ]


# --------------------------------------------------------------------------
# build_nmea2000_message
# --------------------------------------------------------------------------


def test_build_nmea2000_message_returns_object(configured):
    msg = keelson2n2k.build_nmea2000_message(
        129025, "positionRapidUpdate", "Position", _position_fields()
    )
    assert isinstance(msg, NMEA2000Message)
    assert msg.PGN == 129025
    assert msg.source == 1


def test_build_nmea2000_message_none_without_args():
    """Without ARGS (e.g. a stray trigger) no message is built."""
    assert (
        keelson2n2k.build_nmea2000_message(129025, "x", "x", _position_fields()) is None
    )


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------


def test_emit_gateway_mode_sends_to_runner(configured):
    """In gateway mode, emit hands the message object straight to the runner."""
    msg = keelson2n2k.build_nmea2000_message(
        129025, "positionRapidUpdate", "Position", _position_fields()
    )
    runner = Mock()
    keelson2n2k.RUNNER = runner
    keelson2n2k.emit(msg)
    runner.send.assert_called_once_with(msg)


def test_emit_none_is_noop(configured):
    runner = Mock()
    keelson2n2k.RUNNER = runner
    keelson2n2k.emit(None)
    runner.send.assert_not_called()


# --------------------------------------------------------------------------
# End-to-end: inject into a real gateway
# --------------------------------------------------------------------------


@pytest.mark.e2e
def test_emit_injects_into_real_gateway(configured, mock_gateway_server):
    """emit() in gateway mode injects a frame the gateway actually transmits."""
    server = mock_gateway_server(claimed_address=180)

    runner = n2k_gateway.GatewayRunner(
        "yden02",
        host="127.0.0.1",
        port=server.port,
        probe_timeout=1.0,
        stream_received=False,
    )
    runner.start()
    try:
        assert runner.wait_identity(timeout=15.0) is not None
        keelson2n2k.RUNNER = runner

        keelson2n2k.emit(
            keelson2n2k.build_nmea2000_message(
                129025,
                "positionRapidUpdate",
                "Position, Rapid Update",
                _position_fields(),
            )
        )

        deadline = time.monotonic() + 10.0
        injected = set()
        while time.monotonic() < deadline and 129025 not in injected:
            injected = {m.PGN for m in server.received_messages()}
            time.sleep(0.3)
        assert 129025 in injected
    finally:
        runner.stop()


@pytest.mark.e2e
def test_keelson2n2k_gateway_connects_and_probes(
    mock_gateway_server, connector_process_factory, zenoh_endpoints
):
    """keelson2n2k --gateway connects to the gateway and probes its identity."""
    server = mock_gateway_server(claimed_address=180)

    proc = connector_process_factory(
        "nmea",
        "keelson2n2k",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--gateway",
            "yden02",
            "--host",
            "127.0.0.1",
            "--port",
            str(server.port),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    proc.start()
    time.sleep(8)  # connect + ~2s identity probe
    proc.stop()

    # The connect-time identity probe sends an ISO Request (PGN 59904).
    pgns = {m.PGN for m in server.received_messages()}
    assert 59904 in pgns, f"expected an ISO Request from the probe, got {pgns}"
