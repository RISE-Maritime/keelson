"""Unit tests for keelson.scaffolding.rpc (shared RPC-server dispatcher)."""

import logging
from unittest.mock import Mock

import pytest

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.scaffolding import ReplyTracker, RpcOp, reply_err, serve_rpc


def _make_query(request_bytes: bytes = b""):
    query = Mock()
    payload = Mock()
    payload.to_bytes = Mock(return_value=request_bytes)
    query.payload = payload if request_bytes else None
    return query


def _declared_callbacks(session):
    """Map procedure key -> (key, callback) from declare_queryable calls."""
    out = {}
    for call in session.declare_queryable.call_args_list:
        key, callback = call.args[0], call.args[1]
        out[key.split("/@rpc/")[1].split("/")[0]] = (key, callback)
    return out


@pytest.fixture
def session():
    session = Mock()
    session.declare_queryable = Mock(side_effect=lambda *a, **k: Mock())
    return session


@pytest.mark.unit
def test_serve_rpc_declares_one_queryable_per_procedure(session):
    queryables = serve_rpc(
        session,
        base_path="realm",
        entity_id="boat",
        responder_id="conn/0",
        handlers={"play": lambda op: op.reply_ok(), "stop": lambda op: op.reply_ok()},
    )
    assert len(queryables) == 2
    keys = [c.args[0] for c in session.declare_queryable.call_args_list]
    assert "realm/@v0/boat/@rpc/play/conn/0" in keys
    assert "realm/@v0/boat/@rpc/stop/conn/0" in keys
    for c in session.declare_queryable.call_args_list:
        assert c.kwargs["complete"] is True


@pytest.mark.unit
def test_dispatch_ok_reply_reaches_query(session):
    seen = {}

    def handler(op: RpcOp):
        seen["request"] = op.request_bytes
        seen["procedure"] = op.procedure
        op.reply_ok(ErrorResponse(error_description="not-really-an-error"))

    serve_rpc(
        session,
        base_path="realm",
        entity_id="boat",
        responder_id="conn/0",
        handlers={"echo": handler},
    )
    key, callback = _declared_callbacks(session)["echo"]
    query = _make_query(b"hello")
    callback(query)

    assert seen["request"] == b"hello"
    assert seen["procedure"] == "echo"
    reply_key, payload = query.reply.call_args.args
    assert reply_key == key
    decoded = ErrorResponse()
    decoded.ParseFromString(payload)
    assert decoded.error_description == "not-really-an-error"
    query.reply_err.assert_not_called()


@pytest.mark.unit
def test_dispatch_handler_exception_becomes_internal_error(session):
    def handler(op: RpcOp):
        raise RuntimeError("boom")

    serve_rpc(
        session,
        base_path="realm",
        entity_id="boat",
        responder_id="conn/0",
        handlers={"explode": handler},
    )
    _, callback = _declared_callbacks(session)["explode"]
    query = _make_query()
    callback(query)

    err = ErrorResponse()
    err.ParseFromString(query.reply_err.call_args.args[0])
    assert err.code == ErrorResponse.Code.INTERNAL
    assert "boom" in err.error_description


@pytest.mark.unit
def test_dispatch_no_reply_logs_warning(session, caplog):
    serve_rpc(
        session,
        base_path="realm",
        entity_id="boat",
        responder_id="conn/0",
        handlers={"silent": lambda op: None},
    )
    _, callback = _declared_callbacks(session)["silent"]
    with caplog.at_level(logging.WARNING, logger="keelson.scaffolding.rpc"):
        callback(_make_query())
    assert any("without reply" in r.message for r in caplog.records)


@pytest.mark.unit
def test_reply_err_helper_encodes_code_and_swallows_transport_errors():
    query = Mock()
    reply_err(query, "nope", ErrorResponse.Code.INVALID_ARGUMENT)
    err = ErrorResponse()
    err.ParseFromString(query.reply_err.call_args.args[0])
    assert err.code == ErrorResponse.Code.INVALID_ARGUMENT
    assert err.error_description == "nope"

    dead = Mock()
    dead.reply_err = Mock(side_effect=ConnectionError)
    reply_err(dead, "still fine")  # must not raise


@pytest.mark.unit
def test_summarizer_appears_in_audit_log_and_is_error_contained(session, caplog):
    serve_rpc(
        session,
        base_path="realm",
        entity_id="boat",
        responder_id="conn/0",
        handlers={
            "seek": lambda op: op.reply_ok(),
            "bad": lambda op: op.reply_ok(),
        },
        summarizers={
            "seek": lambda b: f"target={b.decode()}",
            "bad": Mock(side_effect=ValueError),
        },
    )
    callbacks = _declared_callbacks(session)
    with caplog.at_level(logging.INFO, logger="keelson.scaffolding.rpc"):
        callbacks["seek"][1](_make_query(b"42"))
        callbacks["bad"][1](_make_query(b"x"))
    messages = [r.getMessage() for r in caplog.records]
    assert any("seek(target=42) called" in m for m in messages)
    assert any("bad(<unparseable>) called" in m for m in messages)
    assert any("seek(target=42) -> OK" in m for m in messages)


@pytest.mark.unit
def test_reply_tracker_tracks_outcome_and_forwards_attributes():
    query = Mock()
    query.custom_attribute = "zenoh-thing"

    tracker = ReplyTracker(query)
    assert tracker.ok is False
    tracker.reply("some/key", b"payload")
    assert tracker.ok is True
    query.reply.assert_called_once_with("some/key", b"payload")
    assert tracker.custom_attribute == "zenoh-thing"

    tracker2 = ReplyTracker(Mock())
    tracker2.reply_err(
        ErrorResponse(
            error_description="denied", code=ErrorResponse.Code.PERMISSION_DENIED
        ).SerializeToString()
    )
    assert tracker2.err_code == "PERMISSION_DENIED"
    assert tracker2.err_text == "denied"

    tracker3 = ReplyTracker(Mock())
    tracker3.reply_err(b"\xff\xff-not-a-proto")
    assert tracker3.err_code == "?"
