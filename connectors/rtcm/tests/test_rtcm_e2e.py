#!/usr/bin/env python3

"""End-to-end tests for the RTCM connector.

Tests actual dataflow with real Zenoh sessions, stdin/stdout piping,
and the ntrip-cli helper binary.
"""

import socket
import time

import pytest

from conftest import RTCM_1005_FRAME


def get_free_port() -> int:
    """Get a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# =============================================================================
# Test 1: rtcm2keelson ingestion via stdin + MCAP recording
# =============================================================================


@pytest.mark.e2e
def test_rtcm2keelson_publishes_to_zenoh(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """rtcm2keelson reads RTCM from stdin and publishes to Zenoh (captured via mcap-record)."""
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
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        stdin_pipe=True,
        binary_mode=True,
    )
    rtcm_in.start()
    time.sleep(1)

    # Write multiple RTCM frames to stdin
    for _ in range(10):
        rtcm_in.write_stdin_bytes(RTCM_1005_FRAME)
        time.sleep(0.1)

    time.sleep(1)
    rtcm_in.close_stdin()
    rtcm_in.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"
    assert mcap_files[0].stat().st_size > 303, "MCAP file should contain published data"


# =============================================================================
# Test 2: keelson2rtcm writes to stdout (full pipeline)
# =============================================================================


@pytest.mark.e2e
def test_keelson2rtcm_writes_to_stdout(connector_process_factory, zenoh_endpoints):
    """Full flow: stdin -> rtcm2keelson -> Zenoh -> keelson2rtcm -> stdout."""
    keelson2rtcm_proc = connector_process_factory(
        "rtcm",
        "keelson2rtcm",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
        binary_mode=True,
    )
    keelson2rtcm_proc.start()
    time.sleep(1)

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
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        stdin_pipe=True,
        binary_mode=True,
    )
    rtcm2keelson_proc.start()
    time.sleep(1)

    # Write RTCM frames to rtcm2keelson stdin
    for _ in range(5):
        rtcm2keelson_proc.write_stdin_bytes(RTCM_1005_FRAME)
        time.sleep(0.1)

    # Give data time to flow through Zenoh
    time.sleep(2)

    rtcm2keelson_proc.close_stdin()
    rtcm2keelson_proc.stop()
    keelson2rtcm_proc.stop()

    # Read keelson2rtcm stdout (binary mode)
    stdout_data = keelson2rtcm_proc.process.stdout.read()
    assert len(stdout_data) > 0, "keelson2rtcm should write data to stdout"
    assert b"\xd3" in stdout_data, "Stdout should contain RTCM v3 preamble byte"


# =============================================================================
# Test 3: ntrip-cli serves data from stdin
# =============================================================================


@pytest.mark.e2e
def test_ntrip_cli_serves_from_stdin(connector_process_factory):
    """ntrip-cli reads RTCM from stdin and serves to NTRIP clients."""
    ntrip_port = get_free_port()

    ntrip_proc = connector_process_factory(
        "rtcm",
        "ntrip-cli",
        [
            "--port",
            str(ntrip_port),
            "--mountpoint",
            "RTCM3",
            "--host",
            "127.0.0.1",
        ],
        stdin_pipe=True,
        binary_mode=True,
    )
    ntrip_proc.start()
    time.sleep(1)

    # Connect as NTRIP client and complete handshake
    sock = socket.create_connection(("127.0.0.1", ntrip_port), timeout=5)
    sock.settimeout(10)
    sock.sendall(b"GET /RTCM3 HTTP/1.1\r\nHost: localhost\r\n\r\n")

    # Read the ICY 200 OK header
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(1024)
        if not chunk:
            break
        header += chunk

    assert b"ICY 200 OK" in header, "Should get NTRIP ICY 200 OK header"

    # Write RTCM data to ntrip-cli stdin
    for _ in range(5):
        ntrip_proc.write_stdin_bytes(RTCM_1005_FRAME)
        time.sleep(0.1)

    # Read data from NTRIP client
    body = sock.recv(4096)
    sock.close()

    ntrip_proc.close_stdin()
    ntrip_proc.stop()

    assert len(body) > 0, "Should receive RTCM data after NTRIP header"
    assert b"\xd3" in body, "Body should contain RTCM v3 preamble byte"


# =============================================================================
# Test 4: Lifecycle — connectors start and stop cleanly
# =============================================================================


@pytest.mark.e2e
def test_lifecycle_start_stop(connector_process_factory, zenoh_endpoints):
    """Both connectors start and stop cleanly without errors."""
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
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
        stdin_pipe=True,
        binary_mode=True,
    )
    rtcm_out = connector_process_factory(
        "rtcm",
        "keelson2rtcm",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        binary_mode=True,
    )

    rtcm_in.start()
    rtcm_out.start()
    time.sleep(1)

    assert rtcm_in.is_running(), "rtcm2keelson should be running"
    assert rtcm_out.is_running(), "keelson2rtcm should be running"

    rtcm_in.close_stdin()
    rtcm_in.stop()
    rtcm_out.stop()

    assert not rtcm_in.is_running(), "rtcm2keelson should have stopped"
    assert not rtcm_out.is_running(), "keelson2rtcm should have stopped"


# =============================================================================
# Test 5: Full pipeline with ntrip-cli
# =============================================================================


@pytest.mark.e2e
def test_full_pipeline_with_ntrip(connector_process_factory, zenoh_endpoints):
    """Full flow: stdin -> rtcm2keelson -> Zenoh -> keelson2rtcm -> ntrip-cli -> NTRIP client.

    Since we can't pipe between subprocesses in tests, we stop keelson2rtcm
    first to collect its stdout, then forward that data to ntrip-cli's stdin.
    """
    ntrip_port = get_free_port()

    # Start ntrip-cli (reads from stdin, serves NTRIP)
    ntrip_proc = connector_process_factory(
        "rtcm",
        "ntrip-cli",
        [
            "--port",
            str(ntrip_port),
            "--mountpoint",
            "RTCM3",
            "--host",
            "127.0.0.1",
        ],
        stdin_pipe=True,
        binary_mode=True,
    )
    ntrip_proc.start()
    time.sleep(1)

    # Connect NTRIP client and complete handshake
    sock = socket.create_connection(("127.0.0.1", ntrip_port), timeout=5)
    sock.settimeout(10)
    sock.sendall(b"GET /RTCM3 HTTP/1.1\r\nHost: localhost\r\n\r\n")

    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(1024)
        if not chunk:
            break
        header += chunk
    assert b"ICY 200 OK" in header

    # Phase 1: Run the Zenoh pipeline to collect RTCM data on keelson2rtcm stdout
    keelson2rtcm_proc = connector_process_factory(
        "rtcm",
        "keelson2rtcm",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
        binary_mode=True,
    )
    keelson2rtcm_proc.start()
    time.sleep(1)

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
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        stdin_pipe=True,
        binary_mode=True,
    )
    rtcm2keelson_proc.start()
    time.sleep(1)

    for _ in range(5):
        rtcm2keelson_proc.write_stdin_bytes(RTCM_1005_FRAME)
        time.sleep(0.1)

    time.sleep(2)

    # Stop Zenoh pipeline — must stop processes before reading stdout
    rtcm2keelson_proc.close_stdin()
    rtcm2keelson_proc.stop()
    keelson2rtcm_proc.stop()

    # Now stdout is readable (process has exited)
    stdout_data = keelson2rtcm_proc.process.stdout.read()

    # Phase 2: Forward collected data to ntrip-cli
    assert len(stdout_data) > 0, "keelson2rtcm should have written data to stdout"

    ntrip_proc.write_stdin_bytes(stdout_data)
    time.sleep(0.5)

    body = sock.recv(4096)
    sock.close()

    ntrip_proc.close_stdin()
    ntrip_proc.stop()

    assert len(body) > 0, "NTRIP client should receive data"
    assert b"\xd3" in body, "NTRIP data should contain RTCM v3 preamble"
