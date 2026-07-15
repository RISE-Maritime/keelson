"""Generic RPC-server dispatch scaffolding.

Connectors that serve a Zenoh RPC interface all need the same plumbing: a
``{procedure: handler}`` registry wired up to one queryable per procedure,
entry/exit/duration audit logging, typed ``ErrorResponse`` replies, and
tracking of whether a handler replied OK or with which error code. This
module provides that once, so connectors only write the handlers.

Handlers receive a single :class:`RpcOp` and reply through it::

    def _handle_play(session, args, op: RpcOp) -> None:
        ...
        op.reply_ok(PlayResponse())

    serve_rpc(
        session,
        base_path=args.realm,
        entity_id=args.entity_id,
        responder_id=args.source_id,
        handlers={"play": functools.partial(_handle_play, session, args)},
    )

Connector-specific context (session, args, a device proxy, ...) is bound
with :func:`functools.partial` or closures; the dispatcher itself is
context-free.

Threading: zenoh-python runs each queryable's callback on its own dedicated
thread, so different procedures execute concurrently while calls to the
same procedure serialise. Long-running handlers therefore never block
telemetry, but must still be thread-safe against each other.
"""

import logging
import time
import traceback
from typing import Any, Callable, Mapping, NamedTuple, Optional

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

logger = logging.getLogger("keelson.scaffolding.rpc")


class ReplyTracker:
    """Wraps a Zenoh query so the dispatch logger can tell whether the
    handler replied OK or with an error, and what code came back, without
    each handler having to thread the outcome up itself. All other
    attribute access is forwarded to the wrapped query."""

    __slots__ = ("_query", "ok", "err_code", "err_text")

    def __init__(self, query):
        self._query = query
        self.ok = False
        self.err_code: Optional[str] = None
        self.err_text: Optional[str] = None

    def reply(self, key_expr, payload):
        self.ok = True
        return self._query.reply(key_expr, payload)

    def reply_err(self, payload):
        try:
            err = ErrorResponse()
            err.ParseFromString(payload)
            self.err_code = ErrorResponse.Code.Name(err.code)
            self.err_text = err.error_description
        except Exception:
            self.err_code = "?"
            self.err_text = "<undecodable>"
        return self._query.reply_err(payload)

    def __getattr__(self, name):
        return getattr(self._query, name)


class RpcOp(NamedTuple):
    """One in-flight RPC call, as seen by a handler."""

    query: Any  # ReplyTracker-wrapped zenoh.Query
    procedure: str
    reply_key: str
    request_bytes: bytes

    def reply_ok(self, response=b"") -> None:
        """Reply successfully with ``response`` (a protobuf message or
        pre-serialized bytes)."""
        payload = (
            response
            if isinstance(response, (bytes, bytearray))
            else response.SerializeToString()
        )
        self.query.reply(self.reply_key, payload)

    def reply_err(
        self, description: str, code: int = ErrorResponse.Code.UNSPECIFIED
    ) -> None:
        """Reply with a typed ``ErrorResponse``."""
        reply_err(self.query, description, code)


def reply_err(
    query, description: str, code: int = ErrorResponse.Code.UNSPECIFIED
) -> None:
    """Reply with a typed ``ErrorResponse`` on ``query``, swallowing (but
    logging) transport failures so a dead client can't take the handler
    thread down with it."""
    try:
        query.reply_err(
            ErrorResponse(error_description=description, code=code).SerializeToString()
        )
    except Exception:
        logger.exception("Failed to reply_err on RPC")


def _make_dispatcher(
    procedure: str,
    reply_key: str,
    handler: Callable[[RpcOp], None],
    summarizer: Optional[Callable[[bytes], str]],
    log: logging.Logger,
):
    def _callback(query) -> None:
        try:
            payload = query.payload
            request_bytes = bytes(payload.to_bytes()) if payload is not None else b""
        except Exception:
            request_bytes = b""

        if summarizer is not None:
            try:
                summary = summarizer(request_bytes)
            except Exception:
                summary = "<unparseable>"
        else:
            summary = ""
        log.info("[RPC] %s(%s) called", procedure, summary)

        tracker = ReplyTracker(query)
        op = RpcOp(
            query=tracker,
            procedure=procedure,
            reply_key=reply_key,
            request_bytes=request_bytes,
        )
        t0 = time.perf_counter()
        try:
            handler(op)
        except Exception:
            log.exception("[RPC] %s handler raised", procedure)
            reply_err(tracker, traceback.format_exc(), ErrorResponse.Code.INTERNAL)
        dur_ms = (time.perf_counter() - t0) * 1000.0

        if tracker.ok:
            log.info("[RPC] %s(%s) -> OK in %.1fms", procedure, summary, dur_ms)
        elif tracker.err_code is not None:
            log.info(
                "[RPC] %s(%s) -> ERR(%s): %s in %.1fms",
                procedure,
                summary,
                tracker.err_code,
                tracker.err_text,
                dur_ms,
            )
        else:
            # Handler returned without replying — shouldn't happen, flag it.
            log.warning(
                "[RPC] %s(%s) handler returned without reply in %.1fms",
                procedure,
                summary,
                dur_ms,
            )

    return _callback


def serve_rpc(
    session,
    *,
    base_path: str,
    entity_id: str,
    responder_id: str,
    handlers: Mapping[str, Callable[[RpcOp], None]],
    summarizers: Optional[Mapping[str, Callable[[bytes], str]]] = None,
    log: Optional[logging.Logger] = None,
) -> list:
    """Declare one queryable per procedure in ``handlers`` and dispatch
    incoming calls to them with audit logging and error containment.

    Each handler is ``Callable[[RpcOp], None]`` and must reply exactly once
    via ``op.reply_ok(...)`` / ``op.reply_err(...)`` (or the raw
    ``op.query.reply*``). A handler that raises gets its traceback returned
    to the caller as ``ErrorResponse.Code.INTERNAL``; a handler that
    returns without replying is logged as a warning.

    ``summarizers`` optionally maps procedure names to
    ``Callable[[bytes], str]`` producing the request summary in the audit
    log line; procedures without an entry log with empty parentheses.

    Returns the list of declared queryables (undeclared automatically on
    session close).
    """
    log = log or logger
    summarizers = summarizers or {}
    queryables = []
    for procedure, handler in handlers.items():
        key = keelson.construct_rpc_key(base_path, entity_id, procedure, responder_id)
        q = session.declare_queryable(
            key,
            _make_dispatcher(procedure, key, handler, summarizers.get(procedure), log),
            complete=True,
        )
        log.debug("[RPC] declared queryable: %s", key)
        queryables.append(q)
    log.info(
        "Declared %d RPC queryables for responder %s", len(queryables), responder_id
    )
    return queryables
