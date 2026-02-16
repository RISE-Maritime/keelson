"""
End-to-end tests for the AIS connector.

Tests actual ais2keelson and keelson2ais process lifecycle
with real Zenoh sessions and sample AIS NMEA data.
"""

import time

import pytest


# Sample AIS NMEA sentences for testing
AIS_MSG1 = "!AIVDM,1,1,,B,15NG6V0P01G?cFhE`R2IU?wn28R>,0*05\n"
AIS_MSG18 = "!AIVDM,1,1,,B,B>eq`d@0>0=dsL8@IHPL@GP00000,0*53\n"


@pytest.mark.e2e
def test_ais2keelson_processes_stdin(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Test that ais2keelson starts, reads AIS NMEA from stdin, and publishes to Zenoh."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    # Start an MCAP recorder to capture published data
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

    # Start ais2keelson with stdin pipe
    ais_proc = connector_process_factory(
        "ais",
        "ais2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "ais-rx",
            "--publish-raw",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        stdin_pipe=True,
    )
    ais_proc.start()
    time.sleep(2)

    # Feed AIS NMEA data (send multiple times to increase chance of capture)
    for _ in range(3):
        ais_proc.write_stdin(AIS_MSG1)
        ais_proc.write_stdin(AIS_MSG18)
        time.sleep(1)

    # Close stdin and stop processes
    ais_proc.close_stdin()
    time.sleep(1)
    ais_proc.stop()
    recorder.stop()

    # Verify MCAP file was created with data
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    mcap_file = mcap_files[0]
    assert (
        mcap_file.stat().st_size > 303
    ), f"MCAP file should contain published AIS data, got {mcap_file.stat().st_size} bytes"


@pytest.mark.e2e
def test_keelson2ais_starts_and_stops(connector_process_factory, zenoh_endpoints):
    """Test that keelson2ais starts with valid arguments and stops gracefully."""
    proc = connector_process_factory(
        "ais",
        "keelson2ais",
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
    )
    proc.start()
    time.sleep(2)

    assert proc.is_running(), "keelson2ais should be running"

    proc.stop()

    assert not proc.is_running(), "keelson2ais should have stopped"
