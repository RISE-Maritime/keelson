"""
End-to-end tests for the MediaMTX connector CLI.

Tests the following command:
- mediamtx: Bridge for WHEP/WebRTC signaling across Zenoh networks
"""

# =============================================================================
# mediamtx CLI tests
# =============================================================================


def test_mediamtx_help(run_in_container):
    """Test that mediamtx --help returns successfully."""
    result = run_in_container("mediamtx --help")

    assert result.returncode == 0
    assert "mediamtx" in result.stdout
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout


def test_mediamtx_missing_required_args(run_in_container):
    """Test that mediamtx fails gracefully when required args are missing."""
    result = run_in_container("mediamtx")

    assert result.returncode != 0


def test_mediamtx_missing_realm_arg(run_in_container):
    """Test that mediamtx fails when --realm is missing."""
    result = run_in_container(
        "mediamtx --entity-id test-entity whep "
        "--whep-host http://localhost:8554 --responder-id test"
    )

    assert result.returncode != 0


def test_mediamtx_missing_entity_id_arg(run_in_container):
    """Test that mediamtx fails when --entity-id is missing."""
    result = run_in_container(
        "mediamtx --realm test-realm whep "
        "--whep-host http://localhost:8554 --responder-id test"
    )

    assert result.returncode != 0


def test_mediamtx_whep_subcommand_help(run_in_container):
    """Test that mediamtx whep --help returns successfully."""
    result = run_in_container("mediamtx --realm test --entity-id test whep --help")

    assert result.returncode == 0
    assert "--whep-host" in result.stdout or "-m" in result.stdout
    assert "--responder-id" in result.stdout or "-i" in result.stdout


def test_mediamtx_whep_missing_required_args(run_in_container):
    """Test that mediamtx whep fails when required args are missing."""
    result = run_in_container("mediamtx --realm test --entity-id test whep")

    assert result.returncode != 0


def test_mediamtx_whep_missing_responder_id(run_in_container):
    """Test that mediamtx whep fails when --responder-id is missing."""
    result = run_in_container(
        "mediamtx --realm test --entity-id test whep "
        "--whep-host http://localhost:8554"
    )

    assert result.returncode != 0


# Note: Full functional testing of mediamtx requires an external MediaMTX server
# which is beyond the scope of these e2e tests. The CLI argument validation
# tests above verify the basic functionality.
