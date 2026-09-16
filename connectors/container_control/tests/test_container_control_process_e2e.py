"""The real process on the real bus: does it serve, and does it let go cleanly?

``test_rpc_e2e.py`` calls ``serve_rpc`` in-process, which proves the handlers
and the key shape but never runs ``main()``. This file spawns the actual
console script, so it covers the wiring nothing else reaches -- and in
particular SIGTERM, which is the entire reason ``init: true`` is in the compose
file. A responder killed without unwinding leaves its liveliness tokens
standing until their lease expires, and every consumer keeps believing it is
alive.

No Docker daemon is needed; a stub Engine stands in (see ``fake_engine.py``).
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager

import pytest

import zenoh

import keelson

from container_control_bins import bin_command

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]

INTERFACE, VERSION = "container_control", "v1"
ENTITY, SOURCE = "testbed", "proc-1"
STARTUP_TIMEOUT_S = 20
SHUTDOWN_TIMEOUT_S = 15


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _client_config(endpoint: str) -> zenoh.Config:
    conf = zenoh.Config()
    # Isolate from the machine: a developer's running router must not be able to
    # answer for, or hold tokens on, the keys under assertion.
    conf.insert_json5("scouting/multicast/enabled", "false")
    conf.insert_json5("scouting/gossip/enabled", "false")
    conf.insert_json5("mode", json.dumps("client"))
    conf.insert_json5("connect/endpoints", json.dumps([endpoint]))
    return conf


@contextmanager
def _spawn(fake_docker_env, *extra_args):
    """The console script, listening on a free port, with a unique realm."""
    port = _free_port()
    endpoint = f"tcp/127.0.0.1:{port}"
    # A realm nobody else can be using, so a stray peer cannot pollute the keys.
    realm = f"test-{uuid.uuid4().hex[:8]}"

    proc = subprocess.Popen(
        [
            *bin_command("docker2keelson"),
            "-r",
            realm,
            "-e",
            ENTITY,
            "-s",
            SOURCE,
            "--mode",
            "peer",
            "--listen",
            endpoint,
            *extra_args,
        ],
        env=fake_docker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"responder exited early: {proc.communicate()[1]}")
        try:
            with zenoh.open(_client_config(endpoint)) as probe:
                key = keelson.construct_rpc_key(
                    realm, ENTITY, INTERFACE, VERSION, "list", SOURCE
                )
                if any(r.ok is not None for r in probe.get(key, timeout=1.0)):
                    break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail(f"responder never answered: {proc.communicate()[1]}")

    try:
        yield proc, endpoint, realm
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=30)


@pytest.fixture
def responder(fake_docker_env):
    with _spawn(fake_docker_env) as spawned:
        yield spawned


@pytest.fixture
def stats_responder(fake_docker_env):
    with _spawn(
        fake_docker_env, "--publish-stats", "*", "--stats-interval-s", "0.2"
    ) as spawned:
        yield spawned


def test_the_spawned_process_answers_rpc(responder):
    from keelson.interfaces.ContainerControl_pb2 import (
        ListContainersRequest,
        ListContainersResponse,
    )

    _proc, endpoint, realm = responder
    key = keelson.construct_rpc_key(realm, ENTITY, INTERFACE, VERSION, "list", SOURCE)
    with zenoh.open(_client_config(endpoint)) as session:
        replies = list(
            session.get(
                key, payload=ListContainersRequest().SerializeToString(), timeout=5.0
            )
        )
    assert replies, "no reply from the spawned responder"
    response = ListContainersResponse.FromString(replies[0].ok.payload.to_bytes())
    # Default deployment shape: read-only until --allow-control says otherwise.
    assert response.control_enabled is False


def _await_token(session, key: str, timeout: float = 10.0) -> bool:
    """Poll until a liveliness token is visible to THIS session.

    Not a bare get: a session that has just connected has not necessarily
    received the token state yet, so an immediate query races propagation and
    fails intermittently -- it passes alone and fails in a full run.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if list(session.liveliness().get(key, timeout=1.0)):
            return True
        time.sleep(0.2)
    return False


def test_sigterm_retracts_the_liveliness_tokens(responder):
    proc, endpoint, realm = responder

    source_token = keelson.construct_source_liveliness_key(realm, ENTITY, SOURCE)
    interface_token = keelson.construct_rpc_interface_liveliness_key(
        realm, ENTITY, INTERFACE, VERSION, SOURCE
    )

    with zenoh.open(_client_config(endpoint)) as session:
        deletes: list[str] = []
        subs = [
            session.liveliness().declare_subscriber(
                key,
                lambda sample: deletes.append(str(sample.key_expr)),
                history=True,
            )
            for key in (source_token, interface_token)
        ]

        # Both tiers are up before we touch it. They are declared by different
        # mechanisms -- declare_liveliness's context manager and serve_rpc --
        # so both are worth asserting.
        for key in (source_token, interface_token):
            assert _await_token(session, key), f"no token at {key}"

        proc.send_signal(signal.SIGTERM)

        # Exit 0 is the proof the handler ran: a process that ignored SIGTERM
        # would be killed by the fixture and report a negative returncode.
        assert proc.wait(timeout=SHUTDOWN_TIMEOUT_S) == 0

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and len(set(deletes)) < 2:
            time.sleep(0.1)
        for sub in subs:
            sub.undeclare()

    stderr = proc.communicate(timeout=30)[1]
    assert "initiating graceful shutdown" in stderr
    assert "Shutting down" in stderr
    # Retracted on the way out, not left to expire by lease -- both tiers, which
    # go by different routes (declare_liveliness's context manager exit, and
    # serve_rpc's token dropped on session close).
    assert set(deletes) == {source_token, interface_token}, deletes


def test_the_spawned_process_publishes_container_stats(stats_responder):
    """The whole publish path in the real process: subject registration, QoS
    derived from the key, enclose, and a real zenoh session.

    The fake engine lists no containers, so the sample is empty -- which is the
    point. It proves the message reaches a subscriber decodable, without
    needing a daemon to have anything running on it.
    """
    from keelson.payloads.ContainerHost_pb2 import ContainerHostStats
    from container_control.stats_publisher import SUBJECT

    _proc, endpoint, realm = stats_responder
    key = keelson.construct_pubsub_key(realm, ENTITY, SUBJECT, SOURCE)

    received = []
    with zenoh.open(_client_config(endpoint)) as session:
        session.declare_subscriber(
            key, lambda sample: received.append(sample.payload.to_bytes())
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline and len(received) < 2:
            time.sleep(0.1)

    assert len(received) >= 2, "no stats samples arrived"

    messages = []
    for payload in received[:2]:
        _r, _e, inner = keelson.uncover(payload)
        message = ContainerHostStats()
        message.ParseFromString(inner)
        messages.append(message)

    # Every tick is a sample: consecutive sequences, not a suppressed repeat.
    assert messages[1].sequence == messages[0].sequence + 1
    assert all(m.HasField("observed_at") for m in messages)


def test_a_reachable_socket_gets_past_the_preflight(fake_docker_env):
    # Started with no --listen and no router to scout, so it will sit in its
    # serve loop; the point is only that it did not exit 1 at the gate.
    proc = subprocess.Popen(
        [*bin_command("docker2keelson", "-r", "test", "-e", "entity", "-s", "source")],
        env=fake_docker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
    finally:
        proc.kill()
        _, stderr = proc.communicate(timeout=30)
    assert "cannot reach the container runtime" not in stderr
    assert "Serving RPC interface container_control/v1" in stderr
