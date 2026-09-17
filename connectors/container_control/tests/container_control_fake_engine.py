"""A minimal stub of the Docker Engine API over a unix socket.

The responder's startup path touches the daemon for exactly two things:
``docker.from_env()`` negotiates the API version with ``GET /version``, and
``DockerBackend.ping()`` calls ``GET /_ping``. Neither needs Docker itself, so
the subprocess tests can drive a real ``docker2keelson`` process --
argparse, zenoh session, serve_rpc, signal handling and all -- on a machine
with no daemon, and in CI.

Anything unrecognised answers ``200 {}`` so a docker-py version bump asking for
one more endpoint cannot break the fixture in a way that looks like a bug in
the responder.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import socketserver
import struct
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

API_VERSION = "1.45"

#: A leading `/v1.45` style API-version prefix, and nothing else.
_VERSION_PREFIX = re.compile(r"^/v\d+\.\d+/")


#: A real one-shot sample, trimmed. cgroup v2, so no `precpu_stats` and
#: `inactive_file` rather than `total_inactive_file` -- which is what the
#: responder actually receives.
_STATS_SAMPLE = {
    "read": "2026-09-04T06:44:12.592948781Z",
    "cpu_stats": {
        "cpu_usage": {"total_usage": 7_063_210_830_000},
        "system_cpu_usage": 755_412_800_000_000,
        "online_cpus": 16,
        "throttling_data": {"periods": 0, "throttled_periods": 0, "throttled_time": 0},
    },
    "memory_stats": {
        "usage": 26_820_608,
        "stats": {"inactive_file": 151_552},
        "limit": 33_364_172_800,
    },
    "pids_stats": {"current": 9, "limit": 34_676},
    "blkio_stats": {
        "io_service_bytes_recursive": [
            {"major": 259, "minor": 2, "op": "read", "value": 16_474_112},
            {"major": 259, "minor": 2, "op": "write", "value": 0},
        ]
    },
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        # Silence per-request stderr noise.
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # BaseHTTPRequestHandler's spelling, not ours
        path = self.path.split("?", 1)[0].rstrip("/")
        # docker-py prefixes /v1.45 once the version is negotiated. Strip only a
        # real version prefix: a bare `startswith("/v")` also eats `/version`
        # itself, which leaves version negotiation answering {} and the client
        # hanging until its timeout.
        path = _VERSION_PREFIX.sub("/", path)

        if path.endswith("/logs"):
            self._serve_logs(path)
        elif path.endswith("/stats"):
            # Answered explicitly rather than falling through to the `{}`
            # default below. That default is not harmless here: an empty body
            # is exactly what a STOPPED container returns, so the sweep would
            # correctly drop every row and an end-to-end stats test would be
            # green and vacuous.
            self._send(_STATS_SAMPLE)
        elif path.endswith("_ping"):
            self.send_response(200)
            self.send_header("Api-Version", API_VERSION)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
        elif path.endswith("version"):
            self._send({"ApiVersion": API_VERSION, "Version": "0.0.0-fake"})
        elif path.endswith("containers/json"):
            self._send([])
        elif path.endswith("/json") and "/containers/" in path:
            # An inspect. Must carry a real Id: docker-py builds every
            # subsequent URL from it, so returning {} sends the follow request
            # to /containers/None/logs and the test deadlocks waiting on a
            # queue nothing feeds.
            name = path.rsplit("/", 2)[-2]
            self._send(
                {
                    "Id": name,
                    "Name": f"/{name}",
                    "Image": "sha256:stub",
                    "Config": {"Tty": False, "Image": "stub:latest", "Labels": {}},
                    "State": {"Status": "running", "StartedAt": "2026-01-01T00:00:00Z"},
                    "HostConfig": {"RestartPolicy": {"Name": "no"}},
                }
            )
        else:
            self._send({})

    def _serve_logs(self, path: str):
        """Stream Docker-multiplexed log frames until the test ends the stream.

        The real daemon holds this connection open and dribbles frames out, which
        is precisely the behaviour the follower has to cope with -- so the stub
        does the same rather than returning a canned body. Frames are written in
        Docker's 8-byte header format (stream byte, three pad, big-endian
        length) because that is what docker-py's multiplexing helper parses.
        """
        name = path.rsplit("/", 2)[-2]
        stream = self.server.log_streams.setdefault(name, queue.Queue())

        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.docker.raw-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        while True:
            item = stream.get()
            if item is _END:
                break
            frame = struct.pack(">BxxxL", 1, len(item)) + item
            try:
                self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ValueError):
                # The follower closed the response to unblock its read. That is
                # the shutdown path working, not a failure.
                return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            pass


class _EndSentinel:
    pass


_END = _EndSentinel()


class _Server(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True

    def get_request(self):
        request, _ = super().get_request()
        # BaseHTTPRequestHandler wants a (host, port) client address.
        return request, ("localhost", 0)


class FakeEngine:
    """Serves the stub on a unix socket until :meth:`stop`.

    Creates its own directory under /tmp rather than taking pytest's
    ``tmp_path``: macOS caps an AF_UNIX path at ~104 bytes, and a
    ``/private/var/folders/.../pytest-of-user/pytest-123/test_name0/`` path
    blows straight through that with ``OSError: AF_UNIX path too long``. The
    caller cannot get this wrong if it is not the caller's decision.
    """

    def __init__(self, socket_path: str | None = None):
        self._owned_dir = None
        if socket_path is None:
            self._owned_dir = tempfile.mkdtemp(prefix="kid-", dir="/tmp")
            socket_path = str(Path(self._owned_dir) / "docker.sock")
        self.socket_path = str(socket_path)
        self._server = _Server(self.socket_path, _Handler)
        self._server.log_streams = {}
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def docker_host(self) -> str:
        return f"unix://{self.socket_path}"

    def __enter__(self) -> FakeEngine:
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self.stop()

    def feed_logs(self, container: str, data: bytes) -> None:
        """Push one frame onto a container's log stream. Frames need not be
        whole lines -- splitting mid-line is the case the follower must handle."""
        self._server.log_streams.setdefault(container, queue.Queue()).put(data)

    def end_logs(self, container: str) -> None:
        """End the stream cleanly, as the daemon does when a container stops."""
        self._server.log_streams.setdefault(container, queue.Queue()).put(_END)

    def stop(self) -> None:
        for stream in self._server.log_streams.values():
            stream.put(_END)
        self._server.shutdown()
        self._server.server_close()
        if self._owned_dir is not None:
            shutil.rmtree(self._owned_dir, ignore_errors=True)
            self._owned_dir = None
