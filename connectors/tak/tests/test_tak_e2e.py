#!/usr/bin/env python3

"""
End-to-end tests for the TAK connector.

Tests actual tak2keelson and keelson2tak process lifecycle.
"""

import time

import pytest


@pytest.mark.e2e
def test_tak2keelson_starts_and_stops(connector_process_factory, zenoh_endpoints):
    """Test that tak2keelson starts with valid arguments and stops gracefully."""
    # Note: will fail to connect to TAK server, but the process should start
    proc = connector_process_factory(
        "tak",
        "tak2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "tak/0",
            "--tak-url",
            "tcp://127.0.0.1:19999",  # no server, expected to retry
            "--reconnect-delay",
            "1.0",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    proc.start()
    time.sleep(2)

    assert proc.is_running(), "tak2keelson should be running (reconnecting)"

    proc.stop()

    assert not proc.is_running(), "tak2keelson should have stopped"


@pytest.mark.e2e
def test_keelson2tak_starts_and_stops(connector_process_factory, zenoh_endpoints):
    """Test that keelson2tak starts with valid arguments and stops gracefully."""
    proc = connector_process_factory(
        "tak",
        "keelson2tak",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--tak-url",
            "tcp://127.0.0.1:19999",  # no server, expected to retry
            "--cot-uid",
            "test-uid-e2e",
            "--cot-callsign",
            "TESTVESSEL",
            "--reconnect-delay",
            "1.0",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    proc.start()
    time.sleep(2)

    assert proc.is_running(), "keelson2tak should be running (reconnecting)"

    proc.stop()

    assert not proc.is_running(), "keelson2tak should have stopped"
