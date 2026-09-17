#!/usr/bin/env python3

"""Tests for n2k2keelson gateway mode (direct CAN-gateway access)."""

import logging
import time
from unittest.mock import Mock

import keelson
import pytest
from keelson.payloads.Primitives_pb2 import TimestampedBytes
from mcap.reader import make_reader
from nmea2000.message import NMEA2000Field, NMEA2000Message

# bin/ is on sys.path via the connector conftest.
import n2k2keelson
import n2k_handlers


@pytest.fixture(autouse=True)
def clear_publishers():
    """Clear the cached publishers between tests to avoid cross-test pollution."""
    n2k2keelson.PUBLISHERS.clear()
    n2k_handlers.UNPARSED_WARNED.clear()
    yield
    n2k2keelson.PUBLISHERS.clear()
    n2k_handlers.UNPARSED_WARNED.clear()


def _position_message():
    """A PGN 129025 Position, Rapid Update message."""
    return NMEA2000Message(
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


# --------------------------------------------------------------------------
# parse_pgn_list
# --------------------------------------------------------------------------


def test_parse_pgn_list():
    assert n2k2keelson.parse_pgn_list("129025, 129026") == [129025, 129026]
    assert n2k2keelson.parse_pgn_list("") is None
    assert n2k2keelson.parse_pgn_list(None) is None


# --------------------------------------------------------------------------
# dispatch_message
# --------------------------------------------------------------------------


def test_dispatch_message_routes_to_handler(mock_zenoh_session):
    """A handled PGN is published under the supplied source_id."""
    n2k2keelson.dispatch_message(
        _position_message(), mock_zenoh_session, "realm", "entity", "n2k/yden02/180"
    )
    publisher = mock_zenoh_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1

    key = mock_zenoh_session.declare_publisher.call_args[0][0]
    assert "location_fix" in key
    assert key.endswith("n2k/yden02/180")


def test_dispatch_message_unknown_pgn_is_noop(mock_zenoh_session):
    """An unhandled PGN publishes nothing."""
    n2k2keelson.dispatch_message(
        NMEA2000Message(PGN=999999, id="unknown"),
        mock_zenoh_session,
        "realm",
        "entity",
        "source",
    )
    mock_zenoh_session.declare_publisher.assert_not_called()


def test_dispatch_message_unknown_pgn_warns_once_per_source(mock_zenoh_session, caplog):
    """An unhandled PGN is a WARNING, once per (PGN, source address)."""
    with caplog.at_level(logging.WARNING, logger="n2k_handlers"):
        for src in (5, 5, 5, 36):
            n2k2keelson.dispatch_message(
                NMEA2000Message(PGN=127252, id="heave", source=src),
                mock_zenoh_session,
                "realm",
                "entity",
                "source",
            )
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "No handler for PGN 127252 (heave) from src 5 "
        "(further occurrences not logged)",
        "No handler for PGN 127252 (heave) from src 36 "
        "(further occurrences not logged)",
    ]


def test_warn_unparsed_once_is_capped(caplog, monkeypatch):
    monkeypatch.setattr(n2k_handlers, "UNPARSED_WARN_LIMIT", 2)
    with caplog.at_level(logging.WARNING, logger="n2k_handlers"):
        for key in range(5):
            n2k_handlers.warn_unparsed_once(key, "unparsed %s", key)
    assert len(n2k_handlers.UNPARSED_WARNED) == 2
    assert [r.getMessage() for r in caplog.records] == [
        "unparsed 0 (further occurrences not logged)",
        "unparsed 1 (further occurrences not logged)",
        "2 distinct unparsed inputs warned about; not warning about more",
    ]


# --------------------------------------------------------------------------
# process_gateway_message
# --------------------------------------------------------------------------


def test_process_gateway_message_publishes(mock_zenoh_session):
    n2k2keelson.process_gateway_message(
        _position_message(), mock_zenoh_session, "r", "e", "s"
    )
    publisher = mock_zenoh_session.declare_publisher.return_value
    assert len(publisher.published_data) == 1


def test_device_source_id_appends_source_address():
    assert (
        n2k2keelson.device_source_id("yden/n2k/yden02/180", _position_message())
        == "yden/n2k/yden02/180/22"
    )


def test_process_gateway_message_keys_on_device_source_address(mock_zenoh_session):
    """Every device on the bus gets its own key, so they are never merged."""
    for src in (5, 22, 36):
        msg = _position_message()
        msg.source = src
        n2k2keelson.process_gateway_message(
            msg, mock_zenoh_session, "r", "e", "yden/n2k/yden02/180"
        )

    keys = [call[0][0] for call in mock_zenoh_session.declare_publisher.call_args_list]
    assert keys == [
        "r/@v0/e/pubsub/location_fix/yden/n2k/yden02/180/5",
        "r/@v0/e/pubsub/location_fix/yden/n2k/yden02/180/22",
        "r/@v0/e/pubsub/location_fix/yden/n2k/yden02/180/36",
    ]


def test_process_gateway_message_instance_follows_device_address(mock_zenoh_session):
    """Handler instance chunks come after the device address."""
    msg = NMEA2000Message(
        PGN=130312,
        id="temperature",
        source=14,
        fields=[
            NMEA2000Field(id="instance", value=0),
            NMEA2000Field(id="source", value="Outside Temperature", raw_value=1),
            NMEA2000Field(id="actualTemperature", value=294.15),
        ],
    )
    n2k2keelson.process_gateway_message(
        msg, mock_zenoh_session, "r", "e", "n2k/yden02/180"
    )
    keys = [call[0][0] for call in mock_zenoh_session.declare_publisher.call_args_list]
    assert keys, "temperature handler published nothing"
    assert all("/n2k/yden02/180/14/" in key for key in keys), keys


def test_publish_raw_frame_text_line_as_utf8(mock_zenoh_session):
    """A text wire unit goes out as bytes on raw_nmea2000 at gateway level."""
    line = "14:54:22.410 R 09F11305 FF 2C AC F0 FF FF FF FF"
    n2k2keelson.publish_raw_frame(
        mock_zenoh_session, "r", "e", "yden/n2k/yden02/180", 1_700_000_000_000, line
    )
    key = mock_zenoh_session.declare_publisher.call_args[0][0]
    assert key == "r/@v0/e/pubsub/raw_nmea2000/yden/n2k/yden02/180"

    envelope = mock_zenoh_session.declare_publisher.return_value.published_data[0]
    _, _, payload_bytes = keelson.uncover(envelope)
    payload = TimestampedBytes()
    payload.ParseFromString(payload_bytes)
    assert payload.value == line.encode()
    assert payload.timestamp.ToNanoseconds() == 1_700_000_000_000


def test_publish_raw_frame_binary_passes_through(mock_zenoh_session):
    n2k2keelson.publish_raw_frame(mock_zenoh_session, "r", "e", "s", 1, b"\x10\x02\x93")
    envelope = mock_zenoh_session.declare_publisher.return_value.published_data[0]
    _, _, payload_bytes = keelson.uncover(envelope)
    payload = TimestampedBytes()
    payload.ParseFromString(payload_bytes)
    assert payload.value == b"\x10\x02\x93"


# --------------------------------------------------------------------------
# DeviceLiveliness
# --------------------------------------------------------------------------


def test_device_liveliness_declares_each_device_once(mock_zenoh_session):
    tokens = []
    liveliness = mock_zenoh_session.liveliness.return_value
    liveliness.declare_token.side_effect = lambda key: tokens.append(key) or Mock()

    with n2k2keelson.DeviceLiveliness(
        mock_zenoh_session, "r", "e", ["heading_magnetic_deg"]
    ) as devices:
        assert devices.ensure("n2k/yden02/180/36") is True
        assert devices.ensure("n2k/yden02/180/36") is False
        assert devices.ensure("n2k/yden02/180/5") is True

    assert "r/@v0/e/pubsub/heading_magnetic_deg/n2k/yden02/180/36" in tokens
    assert "r/@v0/e/pubsub/heading_magnetic_deg/n2k/yden02/180/5" in tokens
    assert len([t for t in tokens if t.endswith("/36")]) == 2  # source + subject


def test_process_gateway_message_survives_errors(mock_zenoh_session):
    """A malformed message must not propagate out of the processing loop."""
    n2k2keelson.process_gateway_message(None, mock_zenoh_session, "r", "e", "s")


# --------------------------------------------------------------------------
# End-to-end: direct-gateway connector against a mock CAN gateway
# --------------------------------------------------------------------------


@pytest.mark.e2e
def test_n2k2keelson_gateway_publishes_with_identity(
    mock_gateway_server, connector_process_factory, temp_dir, zenoh_endpoints
):
    """n2k2keelson --gateway publishes under the probed identity source_id."""
    second_device = _position_message()
    second_device.source = 36
    server = mock_gateway_server(
        claimed_address=180, data_frames=[_position_message(), second_device]
    )

    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(2)

    n2k = connector_process_factory(
        "nmea",
        "n2k2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "n2k/primary",
            "--publish-raw",
            "--gateway",
            "yden02",
            "--host",
            "127.0.0.1",
            "--port",
            str(server.port),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    n2k.start()
    time.sleep(10)  # connect + ~2s identity probe + streaming

    n2k.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"expected 1 MCAP file, found {len(mcap_files)}"

    with open(mcap_files[0], "rb") as handle:
        reader = make_reader(handle)
        topics = {channel.topic for channel in reader.get_summary().channels.values()}

    # Gateway identity, then the N2K source address of each device.
    location_topics = {t for t in topics if "/pubsub/location_fix/" in t}
    assert location_topics == {
        "test-realm/@v0/test-vessel/pubsub/location_fix/n2k/primary/yden02/180/22",
        "test-realm/@v0/test-vessel/pubsub/location_fix/n2k/primary/yden02/180/36",
    }, f"expected one location_fix key per device: {topics}"
    # With --publish-raw, the undecoded bus stream at gateway level.
    assert (
        "test-realm/@v0/test-vessel/pubsub/raw_nmea2000/n2k/primary/yden02/180"
        in topics
    ), topics
