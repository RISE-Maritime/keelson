#!/usr/bin/env python3

"""End-to-end tests for the RTCM connector.

Tests actual dataflow with real Zenoh sessions and real TCP connections.
"""

import socket
import time

import pytest


def get_free_port() -> int:
    """Get a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# =============================================================================
# Test 1: rtcm2keelson ingestion via MCAP recording
# =============================================================================


@pytest.mark.e2e
def test_rtcm2keelson_publishes_to_zenoh(
    connector_process_factory, temp_dir, zenoh_endpoints, mock_rtcm_server
):
    """rtcm2keelson reads from TCP and publishes to Zenoh (captured via mcap-record)."""
    host, port = mock_rtcm_server
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
    time.sleep(1)

    rtcm_in = connector_process_factory(
        "rtcm",
        "rtcm2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "base/0",
            "--host",
            host,
            "--port",
            str(port),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    rtcm_in.start()
    time.sleep(3)

    rtcm_in.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"
    assert mcap_files[0].stat().st_size > 303, "MCAP file should contain published data"


# =============================================================================
# Test 2: keelson2rtcm distribution in TCP mode (full pipeline)
# =============================================================================


@pytest.mark.e2e
def test_full_pipeline_tcp_mode(
    connector_process_factory, zenoh_endpoints, mock_rtcm_server
):
    """Full flow: mock TCP -> rtcm2keelson -> Zenoh -> keelson2rtcm -> TCP client."""
    host, port = mock_rtcm_server
    server_port = get_free_port()

    keelson2rtcm_proc = connector_process_factory(
        "rtcm",
        "keelson2rtcm",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--server-port",
            str(server_port),
            "--server-mode",
            "tcp",
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    keelson2rtcm_proc.start()
    time.sleep(1)

    # Connect TCP client before starting the publisher so the client queue
    # is registered in RTCMDistributor when data starts arriving.
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=5)
    sock.settimeout(10)

    rtcm2keelson_proc = connector_process_factory(
        "rtcm",
        "rtcm2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "base/0",
            "--host",
            host,
            "--port",
            str(port),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    rtcm2keelson_proc.start()

    # Wait for data to flow through the pipeline (Zenoh discovery can take a few seconds)
    data = sock.recv(4096)
    sock.close()

    rtcm2keelson_proc.stop()
    keelson2rtcm_proc.stop()

    assert len(data) > 0, "Should receive data from keelson2rtcm TCP server"
    assert data[0:1] == b"\xd3", "Data should start with RTCM v3 preamble"


# =============================================================================
# Test 3: Full bidirectional pipeline with NTRIP mode
# =============================================================================


@pytest.mark.e2e
def test_full_pipeline_ntrip_mode(
    connector_process_factory, zenoh_endpoints, mock_rtcm_server
):
    """Full flow: mock TCP -> rtcm2keelson -> Zenoh -> keelson2rtcm NTRIP -> client."""
    host, port = mock_rtcm_server
    server_port = get_free_port()

    keelson2rtcm_proc = connector_process_factory(
        "rtcm",
        "keelson2rtcm",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--server-port",
            str(server_port),
            "--server-mode",
            "ntrip",
            "--mountpoint",
            "RTCM3",
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    keelson2rtcm_proc.start()
    time.sleep(1)

    # Connect as NTRIP client and complete the handshake before starting the
    # publisher, so the client queue is registered in RTCMDistributor.
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=5)
    sock.settimeout(10)
    sock.sendall(b"GET /RTCM3 HTTP/1.1\r\nHost: localhost\r\n\r\n")

    # Read the ICY 200 OK header first
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(1024)
        if not chunk:
            break
        header += chunk

    assert b"ICY 200 OK" in header, "Should get NTRIP ICY 200 OK header"

    # Now start the publisher — data will flow to the already-connected client
    rtcm2keelson_proc = connector_process_factory(
        "rtcm",
        "rtcm2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "base/0",
            "--host",
            host,
            "--port",
            str(port),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    rtcm2keelson_proc.start()

    # Wait for RTCM data to arrive through the pipeline
    body = sock.recv(4096)
    sock.close()

    rtcm2keelson_proc.stop()
    keelson2rtcm_proc.stop()

    assert len(body) > 0, "Should receive RTCM data after NTRIP header"
    assert b"\xd3" in body, "Body should contain RTCM v3 preamble byte"


# =============================================================================
# Test 4: Lifecycle — both connectors start and stop cleanly
# =============================================================================


@pytest.mark.e2e
def test_lifecycle_start_stop(
    connector_process_factory, zenoh_endpoints, mock_rtcm_server
):
    """Both connectors start and stop cleanly without errors."""
    host, port = mock_rtcm_server
    server_port = get_free_port()

    rtcm_in = connector_process_factory(
        "rtcm",
        "rtcm2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "base/0",
            "--host",
            host,
            "--port",
            str(port),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    rtcm_out = connector_process_factory(
        "rtcm",
        "keelson2rtcm",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--server-port",
            str(server_port),
            "--server-mode",
            "tcp",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )

    rtcm_in.start()
    rtcm_out.start()
    time.sleep(1)

    assert rtcm_in.is_running(), "rtcm2keelson should be running"
    assert rtcm_out.is_running(), "keelson2rtcm should be running"

    rtcm_in.stop()
    rtcm_out.stop()

    assert not rtcm_in.is_running(), "rtcm2keelson should have stopped"
    assert not rtcm_out.is_running(), "keelson2rtcm should have stopped"
