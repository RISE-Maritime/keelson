#!/usr/bin/env python3

"""Tests for n2k_gateway -- the shared CAN-gateway module."""

import asyncio
import queue
import time

import pytest
from nmea2000.input_formats import N2KFormat
from nmea2000.ioclient import (
    EByteNmea2000Gateway,
    TextNmea2000Gateway,
    WaveShareNmea2000Gateway,
)
from nmea2000.message import NMEA2000Field, NMEA2000Message

# bin/ is on sys.path via the connector conftest.
import n2k_gateway


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class FakeClient:
    """Minimal stand-in for an nmea2000 AsyncIOClient.

    Records sent messages and, after an optional delay, delivers a canned set
    of responses to the registered receive callback -- simulating a gateway
    that echoes injected frames and a bus that answers ISO Requests.
    """

    def __init__(self, responses=None, response_delay=0.0):
        self._callback = None
        self.sent = []
        self._responses = responses or []
        self._delay = response_delay

    def set_receive_callback(self, callback):
        self._callback = callback

    async def send(self, message):
        self.sent.append(message)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._callback is not None:
            for response in self._responses:
                await self._callback(response)


def _iso_request_echo(source):
    """An echoed ISO Request for the Address Claim PGN, from `source`."""
    return NMEA2000Message(
        PGN=n2k_gateway.PGN_ISO_REQUEST,
        id="isoRequest",
        source=source,
        destination=255,
        priority=6,
        fields=[NMEA2000Field(id="pgn", value=60928, raw_value=60928)],
    )


def _address_claim(source):
    """An ISO Address Claim from `source`."""
    return NMEA2000Message(
        PGN=n2k_gateway.PGN_ISO_ADDRESS_CLAIM, id="isoAddressClaim", source=source
    )


# --------------------------------------------------------------------------
# Gateway profiles
# --------------------------------------------------------------------------


def test_profile_registry_integrity():
    """Each profile's name matches its key and the transport is known."""
    for key, profile in n2k_gateway.GATEWAY_PROFILES.items():
        assert profile.name == key
        assert profile.transport in ("tcp", "usb")
        assert callable(profile.builder)


def test_get_profile_valid_and_invalid():
    assert n2k_gateway.get_profile("yden02").name == "yden02"
    with pytest.raises(ValueError, match="Unknown gateway profile"):
        n2k_gateway.get_profile("nonexistent")


# --------------------------------------------------------------------------
# create_gateway
# --------------------------------------------------------------------------


def test_create_gateway_builds_expected_classes():
    """Each profile builds the correct nmea2000 client class."""

    async def run():
        cases = [
            ("yden02", {"host": "h", "port": 1}, TextNmea2000Gateway),
            ("actisense", {"host": "h", "port": 1}, TextNmea2000Gateway),
            ("ebyte", {"host": "h", "port": 1}, EByteNmea2000Gateway),
            ("waveshare", {"device": "/dev/ttyUSB0"}, WaveShareNmea2000Gateway),
        ]
        for profile_name, kwargs, expected_cls in cases:
            client = n2k_gateway.create_gateway(profile_name, **kwargs)
            assert isinstance(client, expected_cls)
            await client.close()

    asyncio.run(run())


def test_create_gateway_yden02_uses_can_frame_ascii():
    """YDEN-02 RAW mode encodes as CAN-frame ASCII; actisense auto-detects."""

    async def run():
        yden = n2k_gateway.create_gateway("yden02", host="h", port=1)
        assert yden.format == N2KFormat.CAN_FRAME_ASCII
        await yden.close()

        actisense = n2k_gateway.create_gateway("actisense", host="h", port=1)
        assert actisense.format is None
        await actisense.close()

    asyncio.run(run())


def test_create_gateway_rejects_unknown_profile():
    with pytest.raises(ValueError, match="Unknown gateway profile"):
        n2k_gateway.create_gateway("bogus", host="h", port=1)


def test_create_gateway_requires_transport_args():
    with pytest.raises(ValueError, match="requires host and port"):
        n2k_gateway.create_gateway("yden02")
    with pytest.raises(ValueError, match="requires a device path"):
        n2k_gateway.create_gateway("waveshare")


# --------------------------------------------------------------------------
# GatewayIdentity
# --------------------------------------------------------------------------


def test_identity_source_id_suffix():
    with_address = n2k_gateway.GatewayIdentity(
        gateway_type="yden02", host="h:1", polite_node=True, claimed_address=180
    )
    assert with_address.source_id_suffix() == "yden02/180"

    without_address = n2k_gateway.GatewayIdentity(
        gateway_type="yden02", host="h:1", polite_node=True
    )
    assert without_address.source_id_suffix() == "yden02"


# --------------------------------------------------------------------------
# ISO request helpers
# --------------------------------------------------------------------------


def test_build_iso_request():
    request = n2k_gateway._build_iso_request(60928)
    assert request.PGN == n2k_gateway.PGN_ISO_REQUEST
    assert request.destination == 255
    assert n2k_gateway._requested_pgn(request) == 60928


def test_requested_pgn_missing_field_returns_none():
    assert (
        n2k_gateway._requested_pgn(NMEA2000Message(PGN=59904, id="isoRequest")) is None
    )


# --------------------------------------------------------------------------
# probe_identity
# --------------------------------------------------------------------------


def test_probe_finds_claimed_address_from_echo():
    """A polite gateway's echoed ISO Request yields its claimed address."""
    client = FakeClient(responses=[_iso_request_echo(180), _address_claim(22)])
    profile = n2k_gateway.GATEWAY_PROFILES["yden02"]

    identity, messages = asyncio.run(
        n2k_gateway.probe_identity(client, profile, "h:1", timeout=0.05)
    )

    assert identity.claimed_address == 180
    assert identity.gateway_type == "yden02"
    assert identity.manufacturer_code == n2k_gateway.MANUFACTURER_YACHT_DEVICES
    assert len(messages) == 2


def test_probe_without_echo_yields_no_address():
    """No echo within the window -> type-only identity."""
    client = FakeClient(responses=[])
    profile = n2k_gateway.GATEWAY_PROFILES["yden02"]

    identity, _ = asyncio.run(
        n2k_gateway.probe_identity(client, profile, "h:1", timeout=0.05)
    )

    assert identity.claimed_address is None
    assert identity.source_id_suffix() == "yden02"


def test_probe_skips_method_a_for_non_polite_gateway():
    """A raw bridge does not rewrite source, so an echo is not a claim."""
    client = FakeClient(responses=[_iso_request_echo(180)])
    profile = n2k_gateway.GATEWAY_PROFILES["ebyte"]

    identity, _ = asyncio.run(
        n2k_gateway.probe_identity(client, profile, "h:1", timeout=0.05)
    )

    assert identity.claimed_address is None
    assert identity.polite_node is False


def test_probe_ignores_echo_outside_window():
    """An echo arriving after the echo window is not taken as the claim."""
    client = FakeClient(responses=[_iso_request_echo(180)], response_delay=0.3)
    profile = n2k_gateway.GATEWAY_PROFILES["yden02"]

    identity, messages = asyncio.run(
        n2k_gateway.probe_identity(client, profile, "h:1", timeout=0.5, echo_window=0.1)
    )

    assert identity.claimed_address is None
    # The frame was still received, just not credited as the echo.
    assert len(messages) == 1


def test_probe_returns_data_frames_seen_during_window():
    """Non-management frames seen while probing are returned for replay."""
    data_frame = NMEA2000Message(PGN=129025, id="positionRapidUpdate", source=7)
    client = FakeClient(responses=[_iso_request_echo(180), data_frame])
    profile = n2k_gateway.GATEWAY_PROFILES["yden02"]

    _, messages = asyncio.run(
        n2k_gateway.probe_identity(client, profile, "h:1", timeout=0.05)
    )

    assert any(m.PGN == 129025 for m in messages)


# --------------------------------------------------------------------------
# GatewayRunner
# --------------------------------------------------------------------------


def test_runner_rejects_unknown_profile():
    with pytest.raises(ValueError, match="Unknown gateway profile"):
        n2k_gateway.GatewayRunner("bogus")


def test_runner_validates_transport_args():
    with pytest.raises(ValueError, match="requires host and port"):
        n2k_gateway.GatewayRunner("yden02")
    with pytest.raises(ValueError, match="requires a device path"):
        n2k_gateway.GatewayRunner("waveshare")


def test_runner_send_before_start_raises():
    runner = n2k_gateway.GatewayRunner("yden02", host="h", port=1)
    with pytest.raises(RuntimeError, match="not running"):
        runner.send(NMEA2000Message(PGN=129025, id="positionRapidUpdate"))


@pytest.mark.e2e
def test_runner_probes_and_streams_against_mock_gateway(mock_gateway_server):
    """GatewayRunner connects to a real socket, probes, and streams frames."""
    position = NMEA2000Message(
        PGN=129025,
        id="positionRapidUpdate",
        source=22,
        destination=255,
        priority=2,
        fields=[
            NMEA2000Field(id="latitude", value=59.5, unit_of_measurement="deg"),
            NMEA2000Field(id="longitude", value=18.25, unit_of_measurement="deg"),
        ],
    )
    server = mock_gateway_server(claimed_address=180, data_frames=[position])

    runner = n2k_gateway.GatewayRunner(
        "yden02", host="127.0.0.1", port=server.port, probe_timeout=1.0
    )
    runner.start()
    try:
        identity = runner.wait_identity(timeout=15.0)
        assert identity is not None
        assert identity.claimed_address == 180
        assert identity.source_id_suffix() == "yden02/180"

        # The streamed position frame should reach the message queue.
        deadline = time.monotonic() + 10.0
        seen_pgns = set()
        while time.monotonic() < deadline and 129025 not in seen_pgns:
            try:
                seen_pgns.add(runner.messages.get(timeout=1.0).PGN)
            except queue.Empty:
                continue
        assert 129025 in seen_pgns
    finally:
        runner.stop()


@pytest.mark.e2e
def test_runner_stream_received_false_drops_frames(mock_gateway_server):
    """With stream_received=False a write-only consumer queues no frames."""
    position = NMEA2000Message(
        PGN=129025,
        id="positionRapidUpdate",
        source=22,
        destination=255,
        priority=2,
        fields=[
            NMEA2000Field(id="latitude", value=59.5, unit_of_measurement="deg"),
            NMEA2000Field(id="longitude", value=18.25, unit_of_measurement="deg"),
        ],
    )
    server = mock_gateway_server(claimed_address=180, data_frames=[position])

    runner = n2k_gateway.GatewayRunner(
        "yden02",
        host="127.0.0.1",
        port=server.port,
        probe_timeout=1.0,
        stream_received=False,
    )
    runner.start()
    try:
        identity = runner.wait_identity(timeout=15.0)
        assert identity is not None
        assert identity.claimed_address == 180
        # The mock keeps streaming frames; none should reach the queue.
        time.sleep(2.0)
        assert runner.messages.empty()
    finally:
        runner.stop()
