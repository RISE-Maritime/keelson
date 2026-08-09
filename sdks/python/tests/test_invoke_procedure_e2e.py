"""E2E contract test between the two RPC SDK surfaces: serve_rpc (server)
and keelson.interfaces.invoke_procedure (generic client), over a real
Zenoh session. The unit tests exercise each side against mocks; this
proves their key construction, payload framing and single-reply
assumptions agree on the wire.

Requires Zenoh in peer mode. Mark all tests with @pytest.mark.e2e.
"""

import time

import pytest
import zenoh

from keelson.interfaces import RpcError, invoke_procedure
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.ReplayControl_pb2 import (
    ReplaySuccessResponse,
    SetSpeedRequest,
)
from keelson.scaffolding import RpcOp, serve_rpc

REALM = "keelson_invoke_e2e"
ENTITY = "test_entity"
RESPONDER = "replay/0"


@pytest.fixture
def session():
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    s = zenoh.open(conf)
    yield s
    s.close()


@pytest.fixture
def replay_control_server(session):
    """Serve a minimal replay_control/v1 with one happy and one erroring
    procedure; 'play' is deliberately left slow-free and unserved elsewhere."""
    seen = {}

    def _set_speed(op: RpcOp):
        req = SetSpeedRequest()
        req.ParseFromString(op.request_bytes)
        seen["speed"] = req.speed
        op.reply_ok(ReplaySuccessResponse())

    def _seek(op: RpcOp):
        op.reply_err("no file loaded", ErrorResponse.Code.INVALID_STATE)

    server = serve_rpc(
        session,
        base_path=REALM,
        entity_id=ENTITY,
        responder_id=RESPONDER,
        interface="replay_control",
        version="v1",
        handlers={"set_speed": _set_speed, "seek": _seek},
    )
    # Give the queryable declarations a moment to settle.
    time.sleep(0.2)
    yield seen
    for q in server.queryables:
        q.undeclare()
    if server.liveliness_token is not None:
        server.liveliness_token.undeclare()


@pytest.mark.e2e
def test_invoke_procedure_ok_round_trip(session, replay_control_server):
    response = invoke_procedure(
        session,
        REALM,
        ENTITY,
        "replay_control",
        "v1",
        "set_speed",
        RESPONDER,
        request=SetSpeedRequest(speed=2.5),
        timeout=5.0,
    )
    # The dynamic response class mirrors the generated one by full name.
    assert response.DESCRIPTOR.full_name == (ReplaySuccessResponse.DESCRIPTOR.full_name)
    assert replay_control_server["speed"] == pytest.approx(2.5)


@pytest.mark.e2e
def test_invoke_procedure_reply_err_raises_typed_rpc_error(
    session, replay_control_server
):
    with pytest.raises(RpcError) as excinfo:
        invoke_procedure(
            session,
            REALM,
            ENTITY,
            "replay_control",
            "v1",
            "seek",
            RESPONDER,
            timeout=5.0,
        )
    assert excinfo.value.code == ErrorResponse.Code.INVALID_STATE
    assert excinfo.value.code_name == "INVALID_STATE"
    assert "no file loaded" in excinfo.value.description


@pytest.mark.e2e
def test_invoke_procedure_times_out_against_unserved_responder(session):
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        invoke_procedure(
            session,
            REALM,
            ENTITY,
            "replay_control",
            "v1",
            "play",
            "nobody/0",
            timeout=1.0,
        )
    # zenoh returns "no more replies" quickly when nothing matches; either
    # way we must be back well before a hung-forever scenario.
    assert time.monotonic() - t0 < 10.0
