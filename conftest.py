"""
Pytest configuration and fixtures for testing Keelson connectors.

These tests run connectors directly from the repository source code,
allowing for fast iteration and testing without Docker.
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest


def get_free_port() -> int:
    """Get a free port number for Zenoh connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Get the repository root directory
REPO_ROOT = Path(__file__).parent
CONNECTORS_DIR = REPO_ROOT / "connectors"
SDK_DIR = REPO_ROOT / "sdks" / "python"

# Mapping from test binary names to actual binary file names
BINARY_NAME_MAP = {
    "foxglove-liveview": "keelson2foxglove.py",
    "mcap-record": "keelson2mcap.py",
    "mcap-replay": "mcap2keelson.py",
    "mcap-tagg": "mcap-tagg.py",
    "mediamtx": "mediamtx-whep.py",
    "mockup_radar": "mockup-radar2keelson.py",
    "platform-geometry": "platform-geometry2keelson.py",
    "klog-record": "keelson2klog.py",
    "klog2mcap": "klog2mcap.py",
    "camera": "camera2keelson.py",
    "ais2keelson": "ais2keelson.py",
    "keelson2ais": "keelson2ais.py",
    "nmea01832keelson": "nmea01832keelson.py",
}


def get_python_interpreter() -> str:
    """
    Get the Python interpreter to use for running connectors.

    Tries sys.executable first, then falls back to system python3 if
    zenoh is not available (e.g., when pytest runs in an isolated venv).
    """
    # First, try sys.executable
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import zenoh"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return sys.executable
    except Exception:
        pass

    # Fall back to system python3
    python3 = shutil.which("python3")
    if python3:
        try:
            result = subprocess.run(
                [python3, "-c", "import zenoh"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return python3
        except Exception:
            pass

    # Last resort: try common paths
    for path in ["/usr/bin/python3", "/usr/local/bin/python3"]:
        if os.path.exists(path):
            try:
                result = subprocess.run(
                    [path, "-c", "import zenoh"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return path
            except Exception:
                pass

    # Default to sys.executable
    return sys.executable


# Determine the Python interpreter to use
PYTHON_EXECUTABLE = get_python_interpreter()


def get_connector_path(connector_name: str, binary_name: str) -> Path:
    """Get the full path to a connector binary."""
    actual_binary = BINARY_NAME_MAP.get(binary_name, binary_name)
    path = CONNECTORS_DIR / connector_name / "bin" / actual_binary
    if not path.exists():
        raise FileNotFoundError(f"Connector not found: {path}")
    return path


def get_python_env() -> dict[str, str]:
    """Get environment variables for running connectors."""
    env = os.environ.copy()

    # Add SDK to PYTHONPATH
    pythonpath = str(SDK_DIR)
    if "PYTHONPATH" in env:
        pythonpath = f"{pythonpath}:{env['PYTHONPATH']}"
    env["PYTHONPATH"] = pythonpath

    return env


class ConnectorProcess:
    """Manages a connector subprocess lifecycle."""

    def __init__(
        self,
        connector: str,
        binary: str,
        args: list[str],
        env: dict[str, str] | None = None,
        stdin_pipe: bool = False,
    ):
        self.connector = connector
        self.binary = binary
        self.args = args
        self.env = env or get_python_env()
        self.process: subprocess.Popen | None = None
        self._script_path = get_connector_path(connector, binary)
        self._stdin_pipe = stdin_pipe

    def start(self) -> None:
        """Start the connector as a background process."""
        cmd = [PYTHON_EXECUTABLE, str(self._script_path)] + self.args
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if self._stdin_pipe else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            text=True,
        )

    def write_stdin(self, data: str) -> None:
        """Write data to the process stdin. Requires stdin_pipe=True."""
        if self.process and self.process.stdin:
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def close_stdin(self) -> None:
        """Close the process stdin to signal EOF."""
        if self.process and self.process.stdin:
            self.process.stdin.close()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the connector process gracefully."""
        if self.process is None:
            return

        if self.process.poll() is None:
            # Send SIGINT for graceful shutdown
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown fails
                self.process.kill()
                self.process.wait()

    def wait(self, timeout: float = 30.0) -> int:
        """Wait for the process to complete and return exit code."""
        if self.process is None:
            return -1
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop()
        return self.process.returncode if self.process else -1

    def is_running(self) -> bool:
        """Check if the process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def logs(self) -> tuple[str, str]:
        """Get stdout and stderr from the process."""
        if self.process is None:
            return "", ""
        stdout = self.process.stdout.read() if self.process.stdout else ""
        stderr = self.process.stderr.read() if self.process.stderr else ""
        return stdout, stderr

    def terminate(self) -> None:
        """Forcefully terminate the process."""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def run_connector_sync(
    connector: str,
    binary: str,
    args: list[str],
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """
    Run a connector synchronously and return the result.

    Args:
        connector: Name of the connector directory (e.g., "mcap", "klog")
        binary: Name of the binary file (e.g., "mcap-record", "klog-record")
        args: Command line arguments to pass to the connector
        timeout: Maximum time to wait for the connector to complete
        env: Environment variables (defaults to get_python_env())

    Returns:
        CompletedProcess with stdout, stderr, and returncode
    """
    script_path = get_connector_path(connector, binary)
    cmd = [PYTHON_EXECUTABLE, str(script_path)] + args
    env = env or get_python_env()

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


@pytest.fixture
def run_connector():
    """
    Fixture that provides a function to run connectors synchronously.

    Usage:
        def test_example(run_connector):
            result = run_connector("mcap", "mcap-record", ["--help"])
            assert result.returncode == 0
    """

    def _run(
        connector: str,
        binary: str,
        args: list[str],
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess:
        return run_connector_sync(connector, binary, args, timeout=timeout)

    return _run


@pytest.fixture
def connector_process_factory():
    """
    Factory fixture for creating connector processes.

    Processes are automatically cleaned up after the test.

    Usage:
        def test_example(connector_process_factory):
            proc = connector_process_factory("mcap", "mcap-record", ["--key", "test/**"])
            proc.start()
            time.sleep(1)
            proc.stop()
    """
    processes: list[ConnectorProcess] = []

    def _create(
        connector: str,
        binary: str,
        args: list[str],
        stdin_pipe: bool = False,
    ) -> ConnectorProcess:
        proc = ConnectorProcess(connector, binary, args, stdin_pipe=stdin_pipe)
        processes.append(proc)
        return proc

    yield _create

    # Cleanup all processes
    for proc in processes:
        proc.terminate()


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """
    Provides a temporary directory that is cleaned up after the test.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
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
    return config_path


@pytest.fixture
def sample_mcap_dir(temp_dir: Path) -> Path:
    """
    Creates a temporary directory for MCAP output.
    """
    mcap_dir = temp_dir / "mcap_output"
    mcap_dir.mkdir()
    return mcap_dir


@pytest.fixture
def zenoh_port() -> int:
    """
    Provides a free port for Zenoh TCP connections.

    Use this to set up explicit listen/connect endpoints for reliable
    peer discovery in environments where multicast doesn't work.

    Usage:
        def test_example(zenoh_port):
            listen_endpoint = f"tcp/127.0.0.1:{zenoh_port}"
            connect_endpoint = f"tcp/127.0.0.1:{zenoh_port}"
    """
    return get_free_port()


@pytest.fixture
def zenoh_endpoints(zenoh_port: int) -> dict[str, str]:
    """
    Provides listen and connect endpoints for Zenoh TCP connections.

    Usage:
        def test_example(zenoh_endpoints):
            recorder_args = ["--listen", zenoh_endpoints["listen"]]
            publisher_args = ["--connect", zenoh_endpoints["connect"]]
    """
    endpoint = f"tcp/127.0.0.1:{zenoh_port}"
    return {
        "listen": endpoint,
        "connect": endpoint,
    }


def wait_for_file(path: Path, timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Wait for a file to exist and have content."""
    start = time.time()
    while time.time() - start < timeout:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(interval)
    return False


def wait_for_condition(
    condition: callable, timeout: float = 10.0, interval: float = 0.5
) -> bool:
    """Wait for a condition to become true."""
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False
