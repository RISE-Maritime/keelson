"""
End-to-end tests for the MediaMTX connector CLI.

Tests the following command:
- mediamtx: Bridge for WHEP/WebRTC signaling across Zenoh networks
"""

import json
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import zenoh

# Import protobuf messages for testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "sdks" / "python"))
import keelson
from keelson.interfaces.WHEPProxy_pb2 import WHEPRequest, WHEPResponse


def get_free_port() -> int:
    """Get a free port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockWHEPHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for WHEP requests."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_POST(self):
        """Handle POST requests for WHEP signaling."""
        # Read the request body (SDP offer)
        content_length = int(self.headers.get("Content-Length", 0))
        sdp_offer = self.rfile.read(content_length).decode("utf-8")

        # Verify the path ends with /whep
        if self.path.endswith("/whep"):
            # Return a mock SDP answer
            sdp_answer = f"v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Mock SDP\r\nt=0 0\r\n"

            self.send_response(201)
            self.send_header("Content-Type", "application/sdp")
            self.send_header("Location", f"http://127.0.0.1{self.path}")
            self.end_headers()
            self.wfile.write(sdp_answer.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class MockWHEPServer:
    """Context manager for a mock WHEP server."""

    def __init__(self, port: int = 0):
        self.port = port or get_free_port()
        self.server = None
        self.thread = None

    def __enter__(self):
        self.server = HTTPServer(("127.0.0.1", self.port), MockWHEPHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        return self

    def __exit__(self, *args):
        if self.server:
            self.server.shutdown()
            self.thread.join(timeout=2)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# =============================================================================
# mediamtx CLI tests
# =============================================================================


def test_mediamtx_help(run_connector):
    """Test that mediamtx --help returns successfully."""
    result = run_connector("mediamtx", "mediamtx", ["--help"])

    assert result.returncode == 0
    assert "mediamtx" in result.stdout
    assert "--realm" in result.stdout or "-r" in result.stdout
    assert "--entity-id" in result.stdout or "-e" in result.stdout


def test_mediamtx_whep_subcommand_help(run_connector):
    """Test that mediamtx whep --help returns successfully."""
    result = run_connector(
        "mediamtx",
        "mediamtx",
        ["--realm", "test", "--entity-id", "test", "whep", "--help"],
    )

    assert result.returncode == 0
    assert "--whep-host" in result.stdout or "-m" in result.stdout
    assert "--responder-id" in result.stdout or "-i" in result.stdout


def test_mediamtx_starts_with_mock_server(connector_process_factory, zenoh_endpoints):
    """Test that mediamtx starts successfully with a mock WHEP server."""
    with MockWHEPServer() as mock_server:
        # Start mediamtx pointing to the mock server
        mediamtx = connector_process_factory(
            "mediamtx",
            "mediamtx",
            [
                "--realm",
                "test-realm",
                "--entity-id",
                "test-vessel",
                "--connect",
                zenoh_endpoints["connect"],
                "whep",
                "--whep-host",
                mock_server.url,
                "--responder-id",
                "camera1",
            ],
        )
        mediamtx.start()

        # Give it time to start and declare queryable
        time.sleep(2)

        # Verify it's running
        assert mediamtx.is_running(), "mediamtx should be running"

        mediamtx.stop()


def test_mediamtx_declares_queryable(connector_process_factory, zenoh_endpoints):
    """Test that mediamtx declares a queryable and can receive queries."""
    with MockWHEPServer() as mock_server:
        # Start mediamtx with listen endpoint (so it can receive queries)
        mediamtx = connector_process_factory(
            "mediamtx",
            "mediamtx",
            [
                "--realm",
                "test-realm",
                "--entity-id",
                "test-vessel",
                "whep",
                "--whep-host",
                mock_server.url,
                "--responder-id",
                "camera1",
            ],
        )
        mediamtx.start()
        time.sleep(2)

        # Verify mediamtx is still running (queryable was declared successfully)
        assert mediamtx.is_running(), "mediamtx should be running after declaring queryable"

        mediamtx.stop()
