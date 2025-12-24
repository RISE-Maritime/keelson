"""
Pytest configuration and fixtures for e2e testing of Keelson connectors.

These tests run connectors inside Docker containers built from the repository's
Dockerfile, testing the actual CLI behavior in an environment identical to
production.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

import pytest


# Default Docker image name used in CI
DEFAULT_IMAGE_NAME = "keelson-ci-image"


@pytest.fixture(scope="session")
def docker_image() -> str:
    """
    Returns the Docker image name to use for testing.

    The image name can be overridden via the KEELSON_TEST_IMAGE environment
    variable. This allows CI to pass the built image name.
    """
    return os.environ.get("KEELSON_TEST_IMAGE", DEFAULT_IMAGE_NAME)


@pytest.fixture(scope="session")
def docker_available() -> bool:
    """Check if Docker is available and the test image exists."""
    try:
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@pytest.fixture(scope="session", autouse=True)
def verify_docker_image(docker_image: str, docker_available: bool) -> None:
    """Verify that the Docker image exists before running tests."""
    if not docker_available:
        pytest.skip("Docker is not available")

    result = subprocess.run(
        ["docker", "image", "inspect", docker_image],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Docker image '{docker_image}' not found. Build it first.")


def run_connector(
    image: str,
    command: str,
    timeout: float = 30.0,
    volumes: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """
    Run a connector command inside the Docker container.

    Args:
        image: Docker image name to use.
        command: The command to run inside the container.
        timeout: Maximum time in seconds to wait for the command.
        volumes: Optional dict mapping host paths to container paths.
        env: Optional dict of environment variables to set.

    Returns:
        CompletedProcess with stdout, stderr, and returncode.
    """
    docker_cmd = ["docker", "run", "--rm"]

    # Add volume mounts
    if volumes:
        for host_path, container_path in volumes.items():
            docker_cmd.extend(["-v", f"{host_path}:{container_path}"])

    # Add environment variables
    if env:
        for key, value in env.items():
            docker_cmd.extend(["-e", f"{key}={value}"])

    docker_cmd.extend([image, command])

    return subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture
def run_in_container(docker_image: str):
    """
    Fixture that provides a function to run commands in the Docker container.

    Usage:
        def test_example(run_in_container):
            result = run_in_container("mcap-record --help")
            assert result.returncode == 0
    """

    def _run(
        command: str,
        timeout: float = 30.0,
        volumes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        return run_connector(
            docker_image,
            command,
            timeout=timeout,
            volumes=volumes,
            env=env,
        )

    return _run


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """
    Provides a temporary directory that is cleaned up after the test.

    The directory is created with permissions that allow Docker containers
    to write to it.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        # Ensure the directory is world-writable for Docker containers
        path.chmod(0o777)
        yield path


@pytest.fixture
def temp_file(temp_dir: Path) -> Iterator[Path]:
    """
    Provides a temporary file path within the temp directory.

    The file is not created, only the path is provided.
    """
    file_path = temp_dir / "test_file"
    yield file_path


@pytest.fixture
def sample_platform_config(temp_dir: Path) -> Path:
    """
    Creates a sample platform configuration file for platform-geometry tests.
    """
    import json

    config = {
        "vessel_name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
        "frame_transforms": [
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "radar",
                "translation": {"x": 5.0, "y": 0.0, "z": 10.0},
                "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            }
        ],
    }

    config_path = temp_dir / "platform_config.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o644)
    return config_path


@pytest.fixture
def sample_mcap_dir(temp_dir: Path) -> Path:
    """
    Creates a temporary directory for MCAP output.
    """
    mcap_dir = temp_dir / "mcap_output"
    mcap_dir.mkdir()
    mcap_dir.chmod(0o777)
    return mcap_dir
