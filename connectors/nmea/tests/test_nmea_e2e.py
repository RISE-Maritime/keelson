"""
End-to-end tests for the NMEA 0183 connector.

Tests actual nmea01832keelson process lifecycle
with real Zenoh sessions and sample NMEA data.
"""

import time

import pytest


# Sample NMEA 0183 sentences for testing
GGA_SENTENCE = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,47.0,M,,*47\n"
RMC_SENTENCE = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\n"
HDT_SENTENCE = "$GPHDT,274.07,T*03\n"


@pytest.mark.e2e
def test_nmea01832keelson_processes_stdin(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """Test that nmea01832keelson reads NMEA from stdin and publishes to Zenoh."""
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

    # Start nmea01832keelson with stdin pipe
    nmea_proc = connector_process_factory(
        "nmea",
        "nmea01832keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "gnss",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        stdin_pipe=True,
    )
    nmea_proc.start()
    time.sleep(2)

    # Feed NMEA data (send multiple times to increase chance of capture)
    for _ in range(3):
        nmea_proc.write_stdin(GGA_SENTENCE)
        nmea_proc.write_stdin(RMC_SENTENCE)
        nmea_proc.write_stdin(HDT_SENTENCE)
        time.sleep(1)

    # Close stdin and stop processes
    nmea_proc.close_stdin()
    time.sleep(1)
    nmea_proc.stop()
    recorder.stop()

    # Verify MCAP file was created with data
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, f"Expected 1 MCAP file, found {len(mcap_files)}"

    mcap_file = mcap_files[0]
    assert (
        mcap_file.stat().st_size > 303
    ), f"MCAP file should contain published NMEA data, got {mcap_file.stat().st_size} bytes"
