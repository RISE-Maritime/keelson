"""The six ``container_control/v1`` procedures.

Every handler replies exactly once on every path. That is the whole contract
``serve_rpc`` cannot enforce for us, and the thing the previous implementation
got wrong: its docker calls sat *outside* their ``try``, so an unknown container
id escaped the callback and the caller learned nothing but a timeout.

Handlers take a :class:`Context` bound with ``functools.partial`` at wiring
time, so this module imports neither ``docker`` nor ``zenoh``.
"""

from __future__ import annotations

import fnmatch
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from google.protobuf.message import DecodeError
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from . import model
from .backend import BackendError
from .guard import ControlGuard
from keelson.interfaces.ContainerControl_pb2 import (
    ContainerActionResponse,
    GetLogsRequest,
    GetLogsResponse,
    ListContainersRequest,
    ListContainersResponse,
    LogStream,
    LogStreamSelector,
    RemoveContainerRequest,
    RemoveContainerResponse,
    RestartContainerRequest,
    StartContainerRequest,
    StopContainerRequest,
)

logger = logging.getLogger("container_control")


@dataclass(frozen=True)
class Limits:
    stop_timeout_s: int = 10
    default_tail_lines: int = 200
    max_tail_lines: int = 5000
    max_log_bytes: int = 1_000_000


@dataclass(frozen=True)
class Context:
    backend: object
    guard: ControlGuard
    limits: Limits = Limits()


def _parse(request_cls, raw: bytes):
    """Decode a request, or raise the INVALID_ARGUMENT the caller should see.

    Empty bytes are a valid request for every message here -- a `list` with no
    options is the common case, and zenoh delivers no payload for it.
    """
    try:
        return request_cls.FromString(raw or b"")
    except (DecodeError, ValueError) as exc:
        raise BackendError(
            ErrorResponse.Code.INVALID_ARGUMENT,
            f"could not decode {request_cls.__name__}: {exc}",
        ) from exc


def _require_name(name: str) -> str:
    if not name:
        raise BackendError(
            ErrorResponse.Code.INVALID_ARGUMENT,
            "request is missing the container name (containers are addressed by "
            "name, not id -- the id changes on every recreate)",
        )
    return name


def _info(ctx: Context, snapshot, *, include_detail: bool = False) -> object:
    return model.build_container_info(
        snapshot,
        controllable=ctx.guard.controllable(snapshot.name, snapshot.id),
        removable=ctx.guard.removable(snapshot.name, snapshot.id),
        include_detail=include_detail,
    )


def handle_list(ctx: Context, op) -> None:
    request = _parse(ListContainersRequest, op.request_bytes)
    snapshots = ctx.backend.list(running_only=request.running_only)

    if request.name_glob:
        snapshots = [
            s for s in snapshots if fnmatch.fnmatchcase(s.name, request.name_glob)
        ]

    response = ListContainersResponse(
        control_enabled=ctx.guard.control_enabled,
        remove_enabled=ctx.guard.remove_enabled,
    )
    response.observed_at.FromNanoseconds(time.time_ns())
    response.containers.extend(
        _info(ctx, s, include_detail=request.include_deployment_detail)
        for s in sorted(snapshots, key=lambda s: s.name)
    )
    op.reply_ok(response)


def handle_logs(ctx: Context, op) -> None:
    request = _parse(GetLogsRequest, op.request_bytes)
    name = _require_name(request.name)

    # Oversize is clamped and reported back, not rejected: a client asking for
    # more history than this responder will serve wants what it can get, and it
    # learns the effective value from the response.
    tail = request.tail_lines or ctx.limits.default_tail_lines
    tail = min(tail, ctx.limits.max_tail_lines)

    selector = request.stream
    want_stdout = selector in (
        LogStreamSelector.LOG_STREAM_SELECTOR_UNSPECIFIED,
        LogStreamSelector.LOG_STREAM_SELECTOR_BOTH,
        LogStreamSelector.LOG_STREAM_SELECTOR_STDOUT,
    )
    want_stderr = selector in (
        LogStreamSelector.LOG_STREAM_SELECTOR_UNSPECIFIED,
        LogStreamSelector.LOG_STREAM_SELECTOR_BOTH,
        LogStreamSelector.LOG_STREAM_SELECTOR_STDERR,
    )

    since = request.since.seconds if request.HasField("since") else None

    out, err, tty, snapshot = ctx.backend.logs(
        name, tail=tail, since=since, want_stdout=want_stdout, want_stderr=want_stderr
    )

    if tty:
        # The runtime merged the streams before we ever saw them; claiming a
        # stream per line would be a guess.
        lines = model.parse_log_stream(out, LogStream.LOG_STREAM_UNSPECIFIED)
    else:
        lines = model.merge_log_lines(
            model.parse_log_stream(out, LogStream.LOG_STREAM_STDOUT),
            model.parse_log_stream(err, LogStream.LOG_STREAM_STDERR),
        )

    lines, truncated = model.cap_log_lines(
        lines, max_lines=tail, max_bytes=ctx.limits.max_log_bytes
    )

    response = GetLogsResponse(
        name=snapshot.name, id=snapshot.id, truncated=truncated, tail_lines=tail
    )
    response.lines.extend(lines)
    op.reply_ok(response)


def _handle_action(verb: str, request_cls, ctx: Context, op) -> None:
    request = _parse(request_cls, op.request_bytes)
    name = _require_name(request.name)

    # Decided BEFORE the container is looked up, so a refused call cannot be
    # used to probe which containers exist on this host.
    decision = ctx.guard.decide(name, verb=verb)
    if not decision.allowed:
        logger.info("[guard] refused %s %r: %s", verb, name, decision.reason)
        op.reply_err(decision.reason, decision.code)
        return

    timeout = getattr(request, "timeout_s", 0) or ctx.limits.stop_timeout_s
    snapshot = getattr(ctx.backend, verb)(name, timeout_s=timeout)
    op.reply_ok(ContainerActionResponse(container=_info(ctx, snapshot)))


def handle_remove(ctx: Context, op) -> None:
    request = _parse(RemoveContainerRequest, op.request_bytes)
    name = _require_name(request.name)

    # Its OWN allow-list, not the control one. A responder permitted to restart
    # every container on the host removes nothing unless --allow-remove says so.
    decision = ctx.guard.decide_remove(name)
    if not decision.allowed:
        logger.info("[guard] refused remove %r: %s", name, decision.reason)
        op.reply_err(decision.reason, decision.code)
        return

    snapshot, was_running = ctx.backend.remove(
        name, force=request.force, remove_volumes=request.remove_volumes
    )
    # WARNING, not info: this is the one procedure here whose effect cannot be
    # undone by calling another one, and the log line is the only record left
    # once the container is gone.
    logger.warning(
        "Removed container %r (id=%s, was_running=%s, volumes=%s)",
        snapshot.name,
        snapshot.id[:12],
        was_running,
        request.remove_volumes,
    )
    op.reply_ok(
        RemoveContainerResponse(
            name=snapshot.name, id=snapshot.id, force_applied=was_running
        )
    )


def _guarded(fn: Callable[..., None], ctx: Context) -> Callable[[object], None]:
    """Turn a handler into the ``Callable[[RpcOp], None]`` serve_rpc wants, and
    convert :class:`BackendError` into the typed reply it stands for.

    Anything not a BackendError is left to propagate: ``serve_rpc`` catches it,
    logs the traceback and replies INTERNAL, which is the right answer for a bug
    in here.
    """

    def _run(op) -> None:
        try:
            fn(ctx, op)
        except BackendError as exc:
            op.reply_err(exc.message, exc.code)

    return _run


def _summary(request_cls, *fields: str) -> Callable[[bytes], str]:
    def _fmt(raw: bytes) -> str:
        request = request_cls.FromString(raw or b"")
        return ", ".join(
            f"{f}={getattr(request, f)!r}" for f in fields if getattr(request, f, None)
        )

    return _fmt


def build(ctx: Context) -> tuple[dict, dict]:
    """Return ``(handlers, summarizers)`` for :func:`keelson.scaffolding.serve_rpc`.

    All six procedures the interface declares are present. ``serve_rpc``'s
    liveliness token advertises the complete interface, so a missing handler
    would advertise a procedure that silently never answers.
    """
    import functools

    handlers = {
        "list": _guarded(handle_list, ctx),
        "logs": _guarded(handle_logs, ctx),
        "start": _guarded(
            functools.partial(_handle_action, "start", StartContainerRequest), ctx
        ),
        "stop": _guarded(
            functools.partial(_handle_action, "stop", StopContainerRequest), ctx
        ),
        "restart": _guarded(
            functools.partial(_handle_action, "restart", RestartContainerRequest), ctx
        ),
        # Not _handle_action: remove answers a different response message, reads
        # a different allow-list, and takes no timeout.
        "remove": _guarded(handle_remove, ctx),
    }
    summarizers = {
        "list": _summary(
            ListContainersRequest,
            "running_only",
            "name_glob",
            "include_deployment_detail",
        ),
        "logs": _summary(GetLogsRequest, "name", "tail_lines"),
        "start": _summary(StartContainerRequest, "name"),
        "stop": _summary(StopContainerRequest, "name", "timeout_s"),
        "restart": _summary(RestartContainerRequest, "name", "timeout_s"),
        "remove": _summary(RemoveContainerRequest, "name", "force", "remove_volumes"),
    }
    return handlers, summarizers
