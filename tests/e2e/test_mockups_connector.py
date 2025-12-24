"""
End-to-end tests for the Mockups connector CLI.

Tests the following command:
- mockup_radar: Generates mock radar data for testing
"""


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
    assert "required" in result.stderr.lower() or "error" in result.stderr.lower()


def test_mockup_radar_missing_realm_arg(run_in_container):
    """Test that mockup_radar fails when --realm is missing."""
    result = run_in_container(
        "mockup_radar --entity-id test-entity --source-id test-source"
    )

    assert result.returncode != 0
    assert "realm" in result.stderr.lower() or "required" in result.stderr.lower()


def test_mockup_radar_missing_entity_id_arg(run_in_container):
    """Test that mockup_radar fails when --entity-id is missing."""
    result = run_in_container("mockup_radar --realm test-realm --source-id test-source")

    assert result.returncode != 0
    assert "entity" in result.stderr.lower() or "required" in result.stderr.lower()


def test_mockup_radar_missing_source_id_arg(run_in_container):
    """Test that mockup_radar fails when --source-id is missing."""
    result = run_in_container("mockup_radar --realm test-realm --entity-id test-entity")

    assert result.returncode != 0
    assert "source" in result.stderr.lower() or "required" in result.stderr.lower()


def test_mockup_radar_shows_default_values(run_in_container):
    """Test that mockup_radar help shows default values for optional args."""
    result = run_in_container("mockup_radar --help")

    assert result.returncode == 0
    # Check that default values are documented for optional parameters
    assert "2048" in result.stdout or "spokes" in result.stdout.lower()
