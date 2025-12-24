"""
End-to-end tests for the Platform connector CLI.

Tests the following command:
- platform-geometry: Publishes static platform geometry information to Zenoh
"""

from pathlib import Path


def test_platform_geometry_help(run_in_container):
    """Test that platform-geometry --help returns successfully."""
    result = run_in_container("platform-geometry --help")

    assert result.returncode == 0
    assert "platform" in result.stdout.lower()
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout
    assert "--source-id" in result.stdout or "-s" in result.stdout
    assert "--config" in result.stdout


def test_platform_geometry_missing_required_args(run_in_container):
    """Test that platform-geometry fails gracefully when required args are missing."""
    result = run_in_container("platform-geometry")

    assert result.returncode != 0


def test_platform_geometry_missing_realm_arg(run_in_container):
    """Test that platform-geometry fails when --realm is missing."""
    result = run_in_container(
        "platform-geometry --entity-id test-entity "
        "--source-id test-source --config /tmp/config.json"
    )

    assert result.returncode != 0


def test_platform_geometry_missing_entity_id_arg(run_in_container):
    """Test that platform-geometry fails when --entity-id is missing."""
    result = run_in_container(
        "platform-geometry --realm test-realm "
        "--source-id test-source --config /tmp/config.json"
    )

    assert result.returncode != 0


def test_platform_geometry_missing_source_id_arg(run_in_container):
    """Test that platform-geometry fails when --source-id is missing."""
    result = run_in_container(
        "platform-geometry --realm test-realm "
        "--entity-id test-entity --config /tmp/config.json"
    )

    assert result.returncode != 0


def test_platform_geometry_missing_config_arg(run_in_container):
    """Test that platform-geometry fails when --config is missing."""
    result = run_in_container(
        "platform-geometry --realm test-realm "
        "--entity-id test-entity --source-id test-source"
    )

    assert result.returncode != 0


def test_platform_geometry_config_file_not_found(run_in_container, temp_dir: Path):
    """Test that platform-geometry fails gracefully when config file doesn't exist."""
    result = run_in_container(
        "platform-geometry --realm test-realm --entity-id test-entity "
        "--source-id test-source --config /data/nonexistent.json",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0


def test_platform_geometry_invalid_json_config(run_in_container, temp_dir: Path):
    """Test that platform-geometry fails gracefully with invalid JSON config."""
    # Create an invalid JSON file
    config_path = temp_dir / "invalid.json"
    config_path.write_text("not valid json {{{")
    config_path.chmod(0o644)

    result = run_in_container(
        "platform-geometry --realm test-realm --entity-id test-entity "
        "--source-id test-source --config /data/invalid.json",
        volumes={str(temp_dir): "/data"},
    )

    assert result.returncode != 0


def test_platform_geometry_shows_optional_args(run_in_container):
    """Test that platform-geometry help documents optional args."""
    result = run_in_container("platform-geometry --help")

    assert result.returncode == 0
    # Check that optional args are documented
    assert "--interval" in result.stdout
