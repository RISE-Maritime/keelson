"""Shared by the signing tests: an isolated loopback bus that hears everything, the key-leak check,
and the connector's escape hatch as a probe that the autopilot accepted a frame."""

from __future__ import annotations

import base64
import json
import socket
import threading
import time

import keelson
import zenoh
from keelson.interfaces.MavlinkCommand_pb2 import (
    CommandLongRequest,
    CommandLongResponse,
)
from keelson.payloads.SensorStatus_pb2 import SensorStatus

MAV_CMD_REQUEST_MESSAGE, MSG_AUTOPILOT_VERSION = 512, 148


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_key(path, key: bytes) -> None:
    path.write_text(key.hex() + "\n")
    path.chmod(0o600)


class Bus:
    """An isolated router; collects every sample, for the key-never-on-the-bus check."""

    def __init__(self, tmp, realm, vessel, source):
        self.realm, self.vessel, self.source = realm, vessel, source
        self.port = free_port()
        conf = zenoh.Config()
        conf.insert_json5("mode", json.dumps("router"))
        conf.insert_json5("scouting/multicast/enabled", "false")
        conf.insert_json5("scouting/gossip/enabled", "false")
        conf.insert_json5(
            "listen/endpoints", json.dumps([f"tcp/127.0.0.1:{self.port}"])
        )
        self.session = zenoh.open(conf)
        self.samples: list[tuple[str, bytes]] = []
        self._lock = threading.Lock()
        # `**` never crosses an `@` chunk, so the keelson tree needs its own subscription.
        self._subs = [
            self.session.declare_subscriber(k, self._on)
            for k in ("**", f"{realm}/@v0/**")
        ]
        self.cfg = tmp / "zenoh.json5"
        self.cfg.write_text(
            json.dumps(
                {
                    "mode": "client",
                    "scouting": {
                        "multicast": {"enabled": False},
                        "gossip": {"enabled": False},
                    },
                    "connect": {"endpoints": [f"tcp/127.0.0.1:{self.port}"]},
                    "listen": {"endpoints": []},
                }
            )
        )

    def _on(self, sample):
        with self._lock:
            self.samples.append((str(sample.key_expr), sample.payload.to_bytes()))

    def keys(self):
        with self._lock:
            return [k for k, _ in self.samples]

    def signing_mode(self):
        key = keelson.construct_pubsub_key(
            self.realm, self.vessel, "sensor_status", f"{self.source}/mavlink_signing"
        )
        with self._lock:
            mine = [p for k, p in self.samples if k == key]
        if not mine:
            return None
        s = SensorStatus()
        s.ParseFromString(keelson.uncover(mine[-1])[2])
        return s.mode

    def close(self):
        for sub in self._subs:
            sub.undeclare()
        self.session.close()

    def heard(self, subject: str) -> bool:
        key = keelson.construct_pubsub_key(
            self.realm, self.vessel, subject, self.source
        )
        return key in self.keys()

    def command(self, timeout: float = 30.0):
        """Ask through the connector's escape hatch for AUTOPILOT_VERSION; its response, or None."""
        key = keelson.construct_rpc_key(
            self.realm,
            self.vessel,
            "mavlink_command",
            "v1",
            "send_command_long",
            self.source,
        )
        req = CommandLongRequest(
            command=MAV_CMD_REQUEST_MESSAGE, param1=MSG_AUTOPILOT_VERSION
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            for reply in self.session.get(
                key, payload=req.SerializeToString(), timeout=5
            ):
                if reply.ok is not None:
                    return CommandLongResponse.FromString(reply.ok.payload.to_bytes())
            time.sleep(0.5)
        return None

    def payload_leaks(self, key: bytes) -> list[str]:
        with self._lock:
            return [k for k, p in self.samples if leaks(p, key)]


def wait(pred, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.25)
    return False


def leaks(text: bytes, key: bytes) -> list[str]:
    """Which spellings of ``key`` appear in ``text``: raw, hex either case, base64."""
    forms = {
        "raw": key,
        "hex": key.hex().encode(),
        "HEX": key.hex().upper().encode(),
        "base64": base64.b64encode(key),
    }
    return [name for name, f in forms.items() if f in text]


def connector(factory, bus, url: str, tmp, *extra: str):
    """mavlink2keelson on ``bus``, at DEBUG so a leak would be in its log."""
    return factory(
        "mavlink",
        "mavlink2keelson",
        [
            "-r",
            bus.realm,
            "-e",
            bus.vessel,
            "--source-id",
            bus.source,
            "--mavlink-url",
            url,
            "--target-system",
            "1",
            "--steering-channel",
            "1",
            "--throttle-channel",
            "3",
            "--config-file",
            str(tmp / "ch.json"),
            "--zenoh-config",
            str(bus.cfg),
            "--log-level",
            "10",
            "--link-timeout",
            "0",
            *extra,
        ],
    )
