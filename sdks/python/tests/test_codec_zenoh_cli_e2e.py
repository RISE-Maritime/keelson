"""E2E: the keelson codecs driven by the real zenoh-cli over a real Zenoh session.

test_codec.py calls the codec functions directly. This proves the other half
of the contract porla pipelines rely on: that zenoh-cli discovers the codecs
by their entry points, hands them each stdin line / sample the way the unit
tests assume, and skips (rather than dies on) a line or sample a codec refuses.

Each test runs `zenoh subscribe` and `zenoh put` as subprocesses, linked over
a loopback TCP endpoint with multicast scouting off, so they find each other
and nothing else.

Requires zenoh-cli (dev dependency group). Mark all tests with @pytest.mark.e2e.
"""

import json
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from base64 import b64encode
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ZENOH = shutil.which("zenoh", path=str(Path(sys.executable).parent))
ISOLATED = ["--mode", "peer", "--cfg", "scouting/multicast/enabled:false"]
TIMEOUT_S = 15

NMEA = ["$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"]
NMEA.append("$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A")


@pytest.fixture(scope="module", autouse=True)
def _require_zenoh_cli():
    assert ZENOH, "zenoh-cli not installed; run `uv sync --group dev`"


@pytest.fixture
def realm():
    """A realm of its own per test, so no test sees another's samples."""
    return f"codec_e2e_{uuid.uuid4().hex[:8]}"


def pubsub_key(realm: str, subject: str, source: str = "porla/0") -> str:
    return f"{realm}/@v0/vessel/pubsub/{subject}/{source}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Subscriber:
    """`zenoh subscribe` printing `<key> <value>` per sample, read on a thread."""

    def __init__(self, realm: str, decoder: str):
        self.realm = realm
        self.port = free_port()
        self.lines: queue.Queue = queue.Queue()
        self.proc = subprocess.Popen(
            [ZENOH, *ISOLATED, "--listen", f"tcp/127.0.0.1:{self.port}"]
            + ["subscribe", "-k", f"{realm}/@v0/vessel/pubsub/**"]
            + ["--decoder", decoder, "--line", "{key} {value}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()
        self._wait_until_ready()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))

    def _wait_until_ready(self):
        """Put a marker until it comes back: the subscription is then live.

        It goes on `raw` via base64, which every decoder can print.
        """
        ready = pubsub_key(self.realm, "raw", "ready")
        deadline = time.monotonic() + TIMEOUT_S
        while time.monotonic() < deadline:
            put(
                self, ready, b64encode(b"ready").decode(), "keelson-enclose-from-base64"
            )
            try:
                while True:
                    if self.lines.get(timeout=0.5).startswith(ready):
                        return
            except queue.Empty:
                continue
        self.close()
        pytest.fail(f"subscriber never became ready: {self.proc.stderr.read()}")

    def received(self, count: int) -> list[tuple[str, str]]:
        """The next `count` samples as (subject, value), in arrival order."""
        out = []
        deadline = time.monotonic() + TIMEOUT_S
        while len(out) < count:
            try:
                line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                pytest.fail(f"expected {count} samples, got {out}")
            key, _, value = line.partition(" ")
            out.append((key.split("/")[4], value))
        return out

    def assert_nothing_more(self, wait_s: float = 1.0):
        time.sleep(wait_s)
        assert self.lines.empty(), list(self.lines.queue)

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=TIMEOUT_S)


@pytest.fixture
def subscriber(realm):
    subs = []

    def start(decoder: str) -> Subscriber:
        subs.append(Subscriber(realm, decoder))
        return subs[-1]

    yield start
    for s in subs:
        s.close()


def put(sub: Subscriber, key, value, encoder, *, stdin=None, line=None):
    """Run `zenoh put` against `sub`, either one -k/-v or lines on stdin."""
    args = [ZENOH, *ISOLATED, "--connect", f"tcp/127.0.0.1:{sub.port}", "put"]
    args += ["--encoder", encoder]
    if key:
        args += ["-k", key]
    if value is not None:
        args += ["-v", value]
    if line:
        args += ["--line", line]
    result = subprocess.run(
        args, input=stdin, capture_output=True, text=True, timeout=TIMEOUT_S
    )
    assert result.returncode == 0, result.stderr
    return result


def test_text_lines_round_trip(realm, subscriber):
    """NMEA lines piped into `zenoh put` read back as sent.

    zenoh-cli rstrips what it prints, so this view cannot show whether a line
    terminator was stored too; test_text_stores_lines_without_terminators does.
    """
    sub = subscriber("keelson-uncover-to-text")

    put(
        sub,
        pubsub_key(realm, "raw_nmea0183"),
        None,
        "keelson-enclose-from-text",
        stdin=f"{NMEA[0]}\r\n{NMEA[1]}\n",
        line="{value}",
    )

    assert sub.received(2) == [("raw_nmea0183", NMEA[0]), ("raw_nmea0183", NMEA[1])]


def test_base64_carries_binary_losslessly(realm, subscriber):
    sub = subscriber("keelson-uncover-to-base64")
    rtcm = b64encode(bytes([0xD3, 0x00, 0x13]) + bytes(range(256))).decode()

    put(sub, pubsub_key(realm, "raw_rtcm_v3"), rtcm, "keelson-enclose-from-base64")

    assert sub.received(1) == [("raw_rtcm_v3", rtcm)]


def test_json_round_trip_structured_subject(realm, subscriber):
    sub = subscriber("keelson-uncover-to-json")
    fix = {"latitude": 57.7, "longitude": 11.9, "altitude": 3.0}

    put(
        sub,
        pubsub_key(realm, "location_fix"),
        json.dumps(fix),
        "keelson-enclose-from-json",
    )

    [(subject, value)] = sub.received(1)
    assert subject == "location_fix"
    assert {k: json.loads(value)[k] for k in fix} == fix


def test_text_stores_lines_without_terminators(realm, subscriber):
    """Text in, json out: json shows the stored value verbatim. Each line is
    stored without its CRLF/LF, in the subject's own type, freshly stamped."""
    sub = subscriber("keelson-uncover-to-json")
    before = time.time()

    put(
        sub,
        pubsub_key(realm, "raw_nmea0183"),
        None,
        "keelson-enclose-from-text",
        stdin=f"{NMEA[0]}\r\n{NMEA[1]}\n",
        line="{value}",
    )

    payloads = [json.loads(value) for _, value in sub.received(2)]
    assert [p["value"] for p in payloads] == NMEA
    for p in payloads:
        # RFC 3339 with nanoseconds; whole seconds are enough here.
        stamped = datetime.fromisoformat(p["timestamp"][:19] + "+00:00")
        assert before - 1 <= stamped.timestamp() <= time.time() + 1


def test_put_skips_refused_lines_and_keeps_going(realm, subscriber):
    """Per-line keys: a line whose subject the text codec refuses (location_fix)
    is logged and skipped by zenoh-cli; the lines around it still arrive."""
    sub = subscriber("keelson-uncover-to-text")
    stdin = "\n".join(
        [
            f"{pubsub_key(realm, 'raw_nmea0183')} {NMEA[0]}",
            f"{pubsub_key(realm, 'location_fix')} not-a-location",
            f"{pubsub_key(realm, 'raw_lwe450')} {NMEA[1]}",
        ]
    )

    result = put(
        sub, None, None, "keelson-enclose-from-text", stdin=stdin, line="{key} {value}"
    )

    assert sub.received(2) == [("raw_nmea0183", NMEA[0]), ("raw_lwe450", NMEA[1])]
    sub.assert_nothing_more()
    assert "json codec" in result.stderr


def test_subscribe_skips_samples_its_decoder_refuses(realm, subscriber):
    """A text subscription over a mixed wildcard prints what fits, skips the rest."""
    sub = subscriber("keelson-uncover-to-text")

    put(
        sub,
        pubsub_key(realm, "location_fix"),
        '{"latitude": 1}',
        "keelson-enclose-from-json",
    )
    put(sub, pubsub_key(realm, "raw_nmea0183"), NMEA[0], "keelson-enclose-from-text")

    assert sub.received(1) == [("raw_nmea0183", NMEA[0])]
    sub.assert_nothing_more()
