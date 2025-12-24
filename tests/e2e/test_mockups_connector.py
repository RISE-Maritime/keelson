"""
End-to-end tests for the Mockups connector CLI.

Tests the following command:
- mockup_radar: Generates mock radar data for testing
"""

import time


# =============================================================================
# mockup_radar CLI tests
# =============================================================================


def test_mockup_radar_help(run_in_container):
    """Test that mockup_radar --help returns successfully."""
    result = run_in_container("mockup_radar --help")

    assert result.returncode == 0
    # The program name in help text might be 'fake_radar' based on the source
    assert "radar" in result.stdout.lower()
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout
    assert "--source-id" in result.stdout or "-s" in result.stdout


def test_mockup_radar_missing_required_args(run_in_container):
    """Test that mockup_radar fails gracefully when required args are missing."""
    result = run_in_container("mockup_radar")

    assert result.returncode != 0


def test_mockup_radar_missing_realm_arg(run_in_container):
    """Test that mockup_radar fails when --realm is missing."""
    result = run_in_container(
        "mockup_radar --entity-id test-entity --source-id test-source"
    )

    assert result.returncode != 0


def test_mockup_radar_missing_entity_id_arg(run_in_container):
    """Test that mockup_radar fails when --entity-id is missing."""
    result = run_in_container("mockup_radar --realm test-realm --source-id test-source")

    assert result.returncode != 0


def test_mockup_radar_missing_source_id_arg(run_in_container):
    """Test that mockup_radar fails when --source-id is missing."""
    result = run_in_container("mockup_radar --realm test-realm --entity-id test-entity")

    assert result.returncode != 0


def test_mockup_radar_shows_optional_args(run_in_container):
    """Test that mockup_radar help documents optional args."""
    result = run_in_container("mockup_radar --help")

    assert result.returncode == 0
    # Check that optional args are documented
    assert "--spokes" in result.stdout or "spokes" in result.stdout.lower()


def test_mockup_radar_generates_data(container_factory, docker_network):
    """Test that mockup_radar runs and generates radar data."""
    # Start mockup_radar with a short duration
    radar = container_factory(
        command=(
            "timeout --signal=INT 3 "
            "mockup_radar --realm test-realm --entity-id test-vessel "
            "--source-id radar1 --spokes_per_sweep 10 --seconds_per_sweep 0.5"
        ),
        network=docker_network.name,
    )
    radar.start()

    # Give it time to generate some data
    time.sleep(1)

    # Verify it's still running (hasn't crashed)
    assert radar.is_running(), "mockup_radar should be running"

    # Wait for it to finish
    exit_code = radar.wait(timeout=10)

    # Check logs for sweep completion
    stdout, stderr = radar.logs()
    # Should show sweep progress in logs
    combined = stdout + stderr
    assert "sweep" in combined.lower() or exit_code == 124  # 124 = timeout


def test_mockup_radar_data_recorded(container_factory, docker_network, temp_dir):
    """Test that mockup_radar data can be recorded by mcap-record."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()
    output_dir.chmod(0o777)

    # Start recorder first
    recorder = container_factory(
        command=(
            "timeout --signal=INT 5 "
            "mcap-record --key 'test-realm/**' --output-folder /data "
            "--mode peer"
        ),
        network=docker_network.name,
        volumes={str(output_dir): "/data"},
    )
    recorder.start()

    time.sleep(1)

    # Start radar publisher
    radar = container_factory(
        command=(
            "timeout --signal=INT 3 "
            "mockup_radar --realm test-realm --entity-id test-vessel "
            "--source-id radar1 --spokes_per_sweep 20 --seconds_per_sweep 1"
        ),
        network=docker_network.name,
    )
    radar.start()

    # Wait for both to finish
    radar.wait(timeout=10)
    recorder.wait(timeout=10)

    # Verify MCAP file contains radar data
    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert (
        file_size > 100
    ), f"MCAP file should contain radar data, got {file_size} bytes"
