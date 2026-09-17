"""End-to-end over a real Zenoh session.

Marked e2e because it binds a TCP port. It is the only test that
exercises serve_rpc's wiring and the actual key expressions -- everything else
calls the handlers directly, which would not notice a key built wrong.
"""

import json
import socket

import pytest

import zenoh

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.scaffolding import serve_rpc

from container_control import handlers
from container_control.guard import ControlGuard
from keelson.interfaces.ContainerControl_pb2 import (
    ContainerActionResponse,
    GetLogsRequest,
    GetLogsResponse,
    ListContainersRequest,
    ListContainersResponse,
    StopContainerRequest,
)
from container_control import (
    INTERFACE,
    VERSION,
)

from container_control_fakes import FakeBackend, snapshot

pytestmark = [
    pytest.mark.e2e,
]

REALM, ENTITY, RESPONDER = "rise", "testbed", "unit-1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(**overrides) -> zenoh.Config:
    conf = zenoh.Config()
    # Isolate from anything else on this machine: no multicast scouting, no
    # gossip. Otherwise a developer's running router joins the test.
    conf.insert_json5("scouting/multicast/enabled", "false")
    conf.insert_json5("scouting/gossip/enabled", "false")
    for key, value in overrides.items():
        conf.insert_json5(key, json.dumps(value))
    return conf


@pytest.fixture(scope="module")
def endpoint():
    return f"tcp/127.0.0.1:{_free_port()}"


@pytest.fixture(scope="module")
def responder(endpoint):
    backend = FakeBackend(
        [
            snapshot("keelson-router", "r" * 64),
            snapshot("grafana", "g" * 64, status="exited"),
        ],
        logs=(b"2026-01-01T00:00:01Z hello\n", b"2026-01-01T00:00:02Z uh oh\n", False),
    )
    ctx = handlers.Context(
        backend=backend,
        guard=ControlGuard(control_enabled=True, allow_globs=("keelson-*",)),
    )
    procedures, summarizers = handlers.build(ctx)
    with zenoh.open(_config(mode="peer", listen={"endpoints": [endpoint]})) as session:
        serve_rpc(
            session,
            base_path=REALM,
            entity_id=ENTITY,
            responder_id=RESPONDER,
            interface=INTERFACE,
            version=VERSION,
            handlers=procedures,
            summarizers=summarizers,
        )
        yield session


@pytest.fixture(scope="module")
def client(responder, endpoint):
    with zenoh.open(
        _config(mode="client", connect={"endpoints": [endpoint]})
    ) as session:
        yield session


def call(client, procedure, request, response_cls):
    key = keelson.construct_rpc_key(
        REALM, ENTITY, INTERFACE, VERSION, procedure, RESPONDER
    )
    for reply in client.get(key, payload=request.SerializeToString(), timeout=5.0):
        if reply.ok is not None:
            return response_cls.FromString(reply.ok.payload.to_bytes())
        return ErrorResponse.FromString(reply.err.payload.to_bytes())
    pytest.fail(f"no reply on {key}")


def test_list_round_trips(client):
    response = call(client, "list", ListContainersRequest(), ListContainersResponse)
    assert [c.name for c in response.containers] == ["grafana", "keelson-router"]
    assert response.control_enabled is True


def test_logs_round_trips_with_both_streams(client):
    response = call(
        client, "logs", GetLogsRequest(name="keelson-router"), GetLogsResponse
    )
    assert [line.text for line in response.lines] == ["hello", "uh oh"]


def test_an_allowed_action_round_trips(client):
    response = call(
        client,
        "stop",
        StopContainerRequest(name="keelson-router"),
        ContainerActionResponse,
    )
    assert response.container.name == "keelson-router"


def test_a_refusal_arrives_as_a_typed_reply_err(client):
    response = call(
        client, "stop", StopContainerRequest(name="grafana"), ContainerActionResponse
    )
    assert isinstance(response, ErrorResponse)
    assert response.code == ErrorResponse.Code.PERMISSION_DENIED
    assert "allow-list" in response.error_description


def test_an_unknown_container_arrives_as_not_found(client):
    response = call(
        client, "logs", GetLogsRequest(name="keelson-ghost"), GetLogsResponse
    )
    assert isinstance(response, ErrorResponse)
    assert response.code == ErrorResponse.Code.NOT_FOUND


def test_the_interface_liveliness_token_is_declared(client):
    token_key = keelson.construct_rpc_interface_liveliness_key(
        REALM, ENTITY, INTERFACE, VERSION, RESPONDER
    )
    replies = list(client.liveliness().get(token_key, timeout=5.0))
    assert replies, f"no liveliness token at {token_key}"
