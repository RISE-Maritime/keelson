"""The six procedures.

The invariant every test here asserts, directly or via `op.replies == 1`: a
handler replies exactly once on every path. The previous implementation left
its docker calls outside the try, so an unknown container id escaped the
callback and the caller got no reply at all -- only a timeout.
"""

import pytest
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from container_control import handlers
from container_control.backend import BackendError
from container_control.guard import ControlGuard
from container_control.handlers import Context, Limits
from keelson.interfaces.ContainerControl_pb2 import (
    ContainerActionResponse,
    GetLogsRequest,
    ListContainersRequest,
    LogStream,
    LogStreamSelector,
    RemoveContainerRequest,
    StopContainerRequest,
)
from keelson.payloads.ContainerHost_pb2 import ContainerState

from container_control_fakes import FakeBackend, FakeOp, snapshot

ALL_PROCEDURES = ("list", "logs", "start", "stop", "restart", "remove")


def run(ctx, procedure, request=None) -> FakeOp:
    op = FakeOp(
        procedure=procedure,
        request_bytes=request.SerializeToString() if request else b"",
    )
    handlers.build(ctx)[0][procedure](op)
    return op


def test_every_declared_procedure_has_a_handler_and_a_summarizer(readonly_ctx):
    procedures, summarizers = handlers.build(readonly_ctx)
    assert sorted(procedures) == sorted(ALL_PROCEDURES)
    assert sorted(summarizers) == sorted(ALL_PROCEDURES)


class TestList:
    def test_empty_payload_is_a_valid_request(self, readonly_ctx):
        # zenoh delivers no payload for a bare GET, and "list everything" is the
        # common case.
        op = run(readonly_ctx, "list")
        assert op.replies == 1 and op.err is None

    def test_returns_every_container_sorted_by_name(self, readonly_ctx):
        op = run(readonly_ctx, "list")
        assert [c.name for c in op.ok.containers] == [
            "grafana",
            "keelson-container-control",
            "keelson-router",
        ]

    def test_running_only_filters(self, readonly_ctx):
        op = run(readonly_ctx, "list", ListContainersRequest(running_only=True))
        assert "grafana" not in [c.name for c in op.ok.containers]

    def test_name_glob_filters(self, readonly_ctx):
        op = run(readonly_ctx, "list", ListContainersRequest(name_glob="keelson-*"))
        assert {c.name for c in op.ok.containers} == {
            "keelson-router",
            "keelson-container-control",
        }

    def test_observed_at_is_set(self, readonly_ctx):
        assert run(readonly_ctx, "list").ok.HasField("observed_at")

    def test_control_enabled_reflects_a_readonly_guard(self, readonly_ctx):
        op = run(readonly_ctx, "list")
        assert op.ok.control_enabled is False
        assert all(not c.controllable for c in op.ok.containers)

    def test_controllable_is_per_container_when_control_is_on(self, control_ctx):
        op = run(control_ctx, "list")
        by_name = {c.name: c.controllable for c in op.ok.containers}
        assert by_name == {
            "keelson-router": True,  # matches the allow-list
            "grafana": False,  # outside it
            "keelson-container-control": False,  # is the responder itself
        }

    def test_a_backend_failure_becomes_a_typed_reply(self):
        ctx = Context(
            backend=FakeBackend(
                raises=BackendError(ErrorResponse.Code.UNAVAILABLE, "socket gone")
            ),
            guard=ControlGuard(),
        )
        op = run(ctx, "list")
        assert op.replies == 1
        assert op.err == ("socket gone", ErrorResponse.Code.UNAVAILABLE)


class TestLogs:
    def _ctx(self, out=b"", err=b"", tty=False, **limits):
        backend = FakeBackend([snapshot("app", "a" * 64)], logs=(out, err, tty))
        return (
            Context(backend=backend, guard=ControlGuard(), limits=Limits(**limits)),
            backend,
        )

    def test_missing_name_is_invalid_argument(self, readonly_ctx):
        op = run(readonly_ctx, "logs", GetLogsRequest())
        assert op.err[1] == ErrorResponse.Code.INVALID_ARGUMENT
        assert "name" in op.err[0]

    def test_streams_are_tagged_and_merged(self):
        ctx, _ = self._ctx(
            out=b"2026-01-01T00:00:01Z out-line\n",
            err=b"2026-01-01T00:00:02Z err-line\n",
        )
        op = run(ctx, "logs", GetLogsRequest(name="app"))
        assert [(line.text, line.stream) for line in op.ok.lines] == [
            ("out-line", LogStream.LOG_STREAM_STDOUT),
            ("err-line", LogStream.LOG_STREAM_STDERR),
        ]

    def test_tty_container_lines_are_untagged(self):
        # The runtime merged the streams before we saw them; a per-line stream
        # would be a guess.
        ctx, _ = self._ctx(out=b"2026-01-01T00:00:01Z merged\n", tty=True)
        op = run(ctx, "logs", GetLogsRequest(name="app"))
        assert op.ok.lines[0].stream == LogStream.LOG_STREAM_UNSPECIFIED

    def test_stream_selector_narrows_the_backend_call(self):
        ctx, backend = self._ctx()
        run(
            ctx,
            "logs",
            GetLogsRequest(
                name="app", stream=LogStreamSelector.LOG_STREAM_SELECTOR_STDERR
            ),
        )
        _, _, _, _, want_stdout, want_stderr = backend.calls[0]
        assert (want_stdout, want_stderr) == (False, True)

    def test_unspecified_selector_means_both(self):
        ctx, backend = self._ctx()
        run(ctx, "logs", GetLogsRequest(name="app"))
        assert backend.calls[0][4:] == (True, True)

    def test_zero_tail_uses_the_responder_default(self):
        ctx, backend = self._ctx(default_tail_lines=42)
        op = run(ctx, "logs", GetLogsRequest(name="app"))
        assert backend.calls[0][2] == 42 and op.ok.tail_lines == 42

    def test_oversize_tail_is_clamped_and_reported_not_rejected(self):
        ctx, backend = self._ctx(max_tail_lines=100)
        op = run(ctx, "logs", GetLogsRequest(name="app", tail_lines=99999))
        assert op.err is None
        assert backend.calls[0][2] == 100
        assert op.ok.tail_lines == 100  # the caller learns the effective value

    def test_truncation_is_reported(self):
        raw = b"".join(b"2026-01-01T00:00:00Z line%d\n" % i for i in range(50))
        ctx, _ = self._ctx(out=raw, default_tail_lines=5)
        op = run(ctx, "logs", GetLogsRequest(name="app"))
        assert op.ok.truncated and len(op.ok.lines) == 5

    def test_unknown_container_is_not_found(self, readonly_ctx):
        op = run(readonly_ctx, "logs", GetLogsRequest(name="nope"))
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.NOT_FOUND


class TestActions:
    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_readonly_refuses_before_touching_the_backend(self, readonly_ctx, verb):
        op = run(readonly_ctx, verb, StopContainerRequest(name="keelson-router"))
        assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED
        assert "--allow-control" in op.err[0]
        # The refusal must not be usable to probe which containers exist.
        assert readonly_ctx.backend.calls == []

    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_allowed_container_succeeds(self, control_ctx, verb):
        op = run(control_ctx, verb, StopContainerRequest(name="keelson-router"))
        assert op.err is None and op.replies == 1
        assert isinstance(op.ok, ContainerActionResponse)

    def test_the_reply_carries_post_action_state(self, control_ctx):
        op = run(control_ctx, "stop", StopContainerRequest(name="keelson-router"))
        assert op.ok.container.state == ContainerState.CONTAINER_STATE_EXITED

    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_container_outside_the_allow_list_is_refused(self, control_ctx, verb):
        op = run(control_ctx, verb, StopContainerRequest(name="grafana"))
        assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED
        assert "allow-list" in op.err[0]
        assert control_ctx.backend.calls == []

    @pytest.mark.parametrize("verb", ["stop", "restart"])
    def test_the_responder_refuses_to_act_on_itself(self, control_ctx, verb):
        op = run(
            control_ctx, verb, StopContainerRequest(name="keelson-container-control")
        )
        assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED
        assert "own container" in op.err[0]

    def test_missing_name_is_invalid_argument(self, control_ctx):
        op = run(control_ctx, "stop", StopContainerRequest())
        assert op.err[1] == ErrorResponse.Code.INVALID_ARGUMENT

    def test_zero_timeout_uses_the_responder_default(self, control_ctx):
        run(
            control_ctx,
            "stop",
            StopContainerRequest(name="keelson-router", timeout_s=0),
        )
        assert control_ctx.backend.calls[0] == ("stop", "keelson-router", 10)

    def test_an_explicit_timeout_is_passed_through(self, control_ctx):
        run(
            control_ctx,
            "stop",
            StopContainerRequest(name="keelson-router", timeout_s=3),
        )
        assert control_ctx.backend.calls[0][2] == 3

    def test_unknown_container_inside_the_allow_list_is_not_found(self, control_ctx):
        op = run(control_ctx, "stop", StopContainerRequest(name="keelson-ghost"))
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.NOT_FOUND

    def test_a_daemon_error_becomes_io_failure(self):
        ctx = Context(
            backend=FakeBackend(
                raises=BackendError(ErrorResponse.Code.IO_FAILURE, "daemon said no")
            ),
            guard=ControlGuard(control_enabled=True, allow_globs=("*",)),
        )
        op = run(ctx, "start", StopContainerRequest(name="x"))
        assert op.err == ("daemon said no", ErrorResponse.Code.IO_FAILURE)


class TestMalformedRequests:
    @pytest.mark.parametrize("procedure", ALL_PROCEDURES)
    def test_garbage_bytes_are_invalid_argument_not_a_crash(
        self, control_ctx, procedure
    ):
        op = FakeOp(procedure=procedure, request_bytes=b"\xff\xff\xff\xff not protobuf")
        handlers.build(control_ctx)[0][procedure](op)
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.INVALID_ARGUMENT


class TestSummarizers:
    def test_summarize_the_named_fields(self, readonly_ctx):
        summarizers = handlers.build(readonly_ctx)[1]
        summary = summarizers["logs"](
            GetLogsRequest(name="app", tail_lines=5).SerializeToString()
        )
        assert "app" in summary and "5" in summary

    @pytest.mark.parametrize("procedure", ALL_PROCEDURES)
    def test_an_empty_request_summarizes_without_raising(self, readonly_ctx, procedure):
        assert handlers.build(readonly_ctx)[1][procedure](b"") == ""


class TestRemove:
    """`remove` is gated by its OWN allow-list, and answers its own message.

    The fixture is deliberately asymmetric -- control covers `keelson-*` and
    `grafana`, removal covers only `grafana` -- so a regression that routed
    remove through the control guard would be caught rather than passing on a
    list that happened to match.
    """

    def test_a_control_enabled_responder_still_refuses_to_remove(self, control_ctx):
        # control_ctx has no remove_globs at all. This is the upgrade case: a
        # deployment that turned control on months ago must not acquire removal
        # by pulling a new image.
        op = run(control_ctx, "remove", RemoveContainerRequest(name="keelson-router"))
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED
        assert "removal is disabled" in op.err[0]

    def test_the_refusal_never_reached_the_daemon(self, control_ctx):
        run(control_ctx, "remove", RemoveContainerRequest(name="keelson-router"))
        assert control_ctx.backend.calls == []

    def test_a_container_in_the_control_list_but_not_the_remove_list_is_refused(
        self, remove_ctx
    ):
        # keelson-router is controllable here and NOT removable. The two lists
        # are read independently; this is the assertion that proves it.
        op = run(remove_ctx, "remove", RemoveContainerRequest(name="keelson-router"))
        assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED
        assert "remove allow-list" in op.err[0]
        assert remove_ctx.backend.calls == []

    def test_removing_a_stopped_container_in_the_list_succeeds(self, remove_ctx):
        op = run(remove_ctx, "remove", RemoveContainerRequest(name="grafana"))
        assert op.replies == 1 and op.err is None
        assert op.ok.name == "grafana"
        assert op.ok.id == "g" * 64
        assert op.ok.force_applied is False

    def test_a_running_container_is_refused_without_force(self, remove_ctx):
        ctx = Context(
            backend=FakeBackend([snapshot("scratch", "c" * 64, status="running")]),
            guard=ControlGuard(
                control_enabled=True, allow_globs=("*",), remove_globs=("scratch",)
            ),
        )
        op = run(ctx, "remove", RemoveContainerRequest(name="scratch"))
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.INVALID_STATE
        assert "stop it first" in op.err[0]

    def test_force_removes_a_running_container_and_says_so(self):
        ctx = Context(
            backend=FakeBackend([snapshot("scratch", "c" * 64, status="running")]),
            guard=ControlGuard(
                control_enabled=True, allow_globs=("*",), remove_globs=("scratch",)
            ),
        )
        op = run(ctx, "remove", RemoveContainerRequest(name="scratch", force=True))
        assert op.err is None
        # The one thing the reply can say that a list cannot: this was running.
        assert op.ok.force_applied is True

    def test_volumes_are_left_alone_unless_asked_for(self, remove_ctx):
        run(remove_ctx, "remove", RemoveContainerRequest(name="grafana"))
        assert ("remove", "grafana", False, False) in remove_ctx.backend.calls

    def test_remove_volumes_reaches_the_backend(self, remove_ctx):
        run(
            remove_ctx,
            "remove",
            RemoveContainerRequest(name="grafana", remove_volumes=True),
        )
        assert ("remove", "grafana", False, True) in remove_ctx.backend.calls

    def test_the_responders_own_container_is_never_removable(self):
        ctx = Context(
            backend=FakeBackend([snapshot("me", "m" * 64, status="exited")]),
            guard=ControlGuard(
                control_enabled=True,
                allow_globs=("*",),
                remove_globs=("*",),
                self_identity=frozenset({"me"}),
            ),
        )
        op = run(ctx, "remove", RemoveContainerRequest(name="me"))
        assert op.err[1] == ErrorResponse.Code.PERMISSION_DENIED
        assert ctx.backend.calls == []

    def test_a_missing_name_is_invalid_argument(self, remove_ctx):
        op = run(remove_ctx, "remove", RemoveContainerRequest(name=""))
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.INVALID_ARGUMENT

    def test_an_unknown_container_inside_the_list_is_not_found(self, remove_ctx):
        ctx = Context(
            backend=FakeBackend([]),
            guard=ControlGuard(
                control_enabled=True, allow_globs=("*",), remove_globs=("*",)
            ),
        )
        op = run(ctx, "remove", RemoveContainerRequest(name="ghost"))
        assert op.replies == 1
        assert op.err[1] == ErrorResponse.Code.NOT_FOUND


class TestPermissionFlagsOnTheListing:
    """What a client greys its buttons on, and the reason there are two flags."""

    def test_a_read_only_responder_reports_neither(self, readonly_ctx):
        op = run(readonly_ctx, "list")
        assert op.ok.control_enabled is False
        assert op.ok.remove_enabled is False
        assert all(not c.controllable and not c.removable for c in op.ok.containers)

    def test_control_without_remove_is_the_common_configuration(self, control_ctx):
        op = run(control_ctx, "list")
        assert op.ok.control_enabled is True
        assert op.ok.remove_enabled is False
        rows = {c.name: c for c in op.ok.containers}
        assert rows["keelson-router"].controllable is True
        assert rows["keelson-router"].removable is False

    def test_the_two_flags_track_their_own_allow_lists(self, remove_ctx):
        op = run(remove_ctx, "list")
        assert op.ok.control_enabled is True and op.ok.remove_enabled is True
        rows = {c.name: c for c in op.ok.containers}
        # Controllable, not removable.
        assert rows["keelson-router"].controllable is True
        assert rows["keelson-router"].removable is False
        # Both -- it is in each list.
        assert rows["grafana"].controllable is True
        assert rows["grafana"].removable is True
        # Neither: the responder's own container, whichever list names it.
        assert rows["keelson-container-control"].controllable is False
        assert rows["keelson-container-control"].removable is False


class TestDeploymentDetail:
    @staticmethod
    def _ctx() -> Context:
        snap = snapshot("app", "a" * 64)
        snap.attrs["Config"]["Env"] = ["API_TOKEN=hunter2"]
        snap.attrs["Mounts"] = [{"Type": "volume", "Name": "data", "Destination": "/d"}]
        return Context(backend=FakeBackend([snap]), guard=ControlGuard())

    def test_list_leaves_detail_off_unless_asked(self):
        (container,) = run(self._ctx(), "list").ok.containers
        assert list(container.mounts) == []

    def test_list_fills_detail_when_asked(self):
        op = run(
            self._ctx(), "list", ListContainersRequest(include_deployment_detail=True)
        )
        (container,) = op.ok.containers
        assert [m.source for m in container.mounts] == ["data"]
        assert b"hunter2" not in op.ok.SerializeToString()
