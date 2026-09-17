"""The Docker Engine adapter.

The only module that imports ``docker``. It hands the rest of the process
plain :class:`~.model.ContainerSnapshot` objects and a single
:class:`BackendError`, so no handler ever has to know a docker-py exception
type -- and, more to the point, no docker-py exception can escape a queryable
callback and leave the caller with no reply at all, which is what the previous
implementation did for every unknown container id.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence

import docker
from docker.errors import APIError, DockerException, NotFound
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from .model import ContainerSnapshot

logger = logging.getLogger("container_control.backend")


class BackendError(Exception):
    """A runtime failure already mapped to the code the caller will see."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _snapshot(container) -> ContainerSnapshot:
    image_tags: Sequence[str] = ()
    image_id = ""
    try:
        image = container.image
        if image is not None:
            image_tags = tuple(image.tags or ())
            image_id = image.id or ""
    except (APIError, DockerException):
        # An image deleted out from under a running container is not a reason to
        # drop the container from the listing.
        logger.debug("Could not read image for %s", container.name, exc_info=True)
    return ContainerSnapshot(
        name=container.name or "",
        id=container.id or "",
        attrs=container.attrs or {},
        image_tags=image_tags,
        image_id=image_id,
        labels=dict(container.labels or {}),
    )


class DockerBackend:
    """Thread-local docker-py client plus the six operations we need.

    zenoh runs each queryable's callback on its own dedicated thread, and
    docker-py's client is not documented as thread-safe, so each thread gets its
    own. Unlike the previous ``docker.from_env()`` at every one of five call
    sites, this does not pay for a fresh unix connection and an API version
    negotiation on every single RPC.

    Thread-local is also what keeps log following out of trouble. A client's
    connection pool holds ``DEFAULT_MAX_POOL_SIZE = 10``, and a follow stream
    holds its connection for as long as it runs -- so more than ten follows
    sharing one client would block or leak sockets. One client per follower
    thread means each holds a single connection in a pool of its own. The cost
    is a connection and a version negotiation per followed container, paid once
    at thread start.
    """

    def __init__(self, **client_kwargs):
        self._client_kwargs = client_kwargs
        self._local = threading.local()

    @property
    def client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            try:
                client = docker.from_env(**self._client_kwargs)
            except DockerException as exc:
                raise BackendError(
                    ErrorResponse.Code.UNAVAILABLE,
                    f"cannot reach the container runtime: {exc}",
                ) from exc
            self._local.client = client
        return client

    def ping(self) -> None:
        """Fail at startup rather than on the operator's first click."""
        try:
            self.client.ping()
        except BackendError:
            raise
        except (APIError, DockerException) as exc:
            raise BackendError(
                ErrorResponse.Code.UNAVAILABLE,
                f"cannot reach the container runtime: {exc}",
            ) from exc

    def list(
        self, *, running_only: bool = False, label: str | None = None
    ) -> list[ContainerSnapshot]:
        filters = {"label": label} if label else None
        try:
            containers = self.client.containers.list(
                all=not running_only, filters=filters
            )
        except (APIError, DockerException) as exc:
            raise self._translate(exc, "list containers") from exc
        return [_snapshot(c) for c in containers]

    def get(self, name_or_id: str) -> ContainerSnapshot:
        try:
            return _snapshot(self.client.containers.get(name_or_id))
        except (APIError, DockerException) as exc:
            raise self._translate(exc, "get container", name_or_id) from exc

    def stats(self, name_or_id: str) -> dict:
        """One instantaneous sample of a container's resource counters.

        ``one_shot`` is the whole reason this is usable on an interval. Without
        it the daemon takes two readings a second apart so it can hand back a
        pre-computed delta, and that second is spent per container, serialised:
        eight containers cost eight seconds, which does not fit inside any
        sensible publish interval. With it the same eight return in under a
        tenth of a second.

        The trade is that ``precpu_stats`` comes back zeroed -- there is no
        earlier reading for the daemon to have taken -- so the caller keeps the
        previous sample and does the differencing itself. See
        :func:`model.build_resource_usage`.

        Through ``client.api`` rather than ``containers.get(id).stats(...)``,
        unlike everything else in this class: the model-object route inspects
        the container first, and :meth:`list` inspected every one of them moments
        ago. That is one wasted round trip per container per tick, forever.

        Returns the raw dictionary rather than a translated type: everything
        that interprets it lives in :mod:`model`, which is kept free of both
        docker and zenoh.
        """
        try:
            return self.client.api.stats(name_or_id, stream=False, one_shot=True) or {}
        except (APIError, DockerException) as exc:
            raise self._translate(exc, "read stats", name_or_id) from exc

    def logs(
        self,
        name: str,
        *,
        tail: int,
        since: int | None = None,
        want_stdout: bool = True,
        want_stderr: bool = True,
    ) -> tuple[bytes, bytes, bool, ContainerSnapshot]:
        """Return ``(stdout, stderr, tty, snapshot)``.

        docker-py's ``logs()`` has no ``demux``, so the two streams are fetched
        separately and merged on their timestamps by :func:`model.merge_log_lines`.
        A TTY-allocated container has had its streams merged by the runtime long
        before we see them, so it gets one untagged call instead.
        """
        try:
            container = self.client.containers.get(name)
            tty = bool(((container.attrs or {}).get("Config") or {}).get("Tty"))
            common = {
                "timestamps": True,
                "tail": tail if tail > 0 else "all",
                "stream": False,
            }
            if since is not None:
                common["since"] = since

            if tty:
                merged = container.logs(stdout=True, stderr=True, **common)
                return merged or b"", b"", True, _snapshot(container)

            out = (
                container.logs(stdout=True, stderr=False, **common)
                if want_stdout
                else b""
            )
            err = (
                container.logs(stdout=False, stderr=True, **common)
                if want_stderr
                else b""
            )
            return out or b"", err or b"", False, _snapshot(container)
        except (APIError, DockerException) as exc:
            raise self._translate(exc, "read logs", name) from exc

    def follow_logs(self, name: str, *, since: float | None = None, tail: int = 0):
        """Open a following log stream. Returns ``(frames, closer, snapshot)``.

        ``closer`` is the only way to stop this. docker-py disables the socket
        timeout on a streaming read, so the generator blocks forever on a quiet
        container and its ``next()`` can never serve as a shutdown check -- the
        supervisor has to close the underlying response from another thread,
        which makes the blocked read return.

        stdout and stderr are merged into one call deliberately. docker-py's
        ``logs()`` has no ``demux`` and its multiplexing helper discards the
        stream byte, so tagging them apart would cost a second connection and a
        second thread per container -- and ``foxglove.Log`` has no stream field
        to carry the result anyway.
        """
        try:
            container = self.client.containers.get(name)
            kwargs = {
                "stdout": True,
                "stderr": True,
                "stream": True,  # implies follow=True in docker-py
                "timestamps": True,
                "tail": tail if tail > 0 else 0,
            }
            # Must be > 0: docker-py raises InvalidArgument on 0, so "from the
            # beginning" is expressed by omitting it, not by passing zero.
            if since is not None and since > 0:
                kwargs["since"] = since
            frames = container.logs(**kwargs)
            return frames, _closer_for(frames), _snapshot(container)
        except (APIError, DockerException) as exc:
            raise self._translate(exc, "follow logs", name) from exc

    def start(self, name: str, timeout_s: int = 0) -> ContainerSnapshot:
        return self._act(name, "start")

    def stop(self, name: str, timeout_s: int = 0) -> ContainerSnapshot:
        return self._act(name, "stop", timeout=timeout_s)

    def restart(self, name: str, timeout_s: int = 0) -> ContainerSnapshot:
        return self._act(name, "restart", timeout=timeout_s)

    def remove(
        self, name: str, *, force: bool = False, remove_volumes: bool = False
    ) -> tuple[ContainerSnapshot, bool]:
        """Delete a container. Returns ``(snapshot as it last was, was_running)``.

        NOT ``_act``, and not because of the different return type. ``_act``
        ends in ``container.reload()`` to report post-action state; there is no
        post-action state here, and reload() on a removed container raises
        NotFound -- which would surface as "no such container" for a removal
        that had in fact just succeeded. The snapshot is taken BEFORE the call
        instead, and is the last true thing anyone can say about it.

        The running check is done here, from the attrs we already hold, rather
        than by letting the daemon's 409 come back: one round trip either way,
        but this one produces INVALID_STATE and names the stop the operator
        needs, where the 409 arrives as an IO_FAILURE wrapping a sentence about
        the Engine API. The daemon remains the authority -- a container that
        starts between our read and the delete still gets its 409, which is why
        the except below stays.
        """
        try:
            container = self.client.containers.get(name)
            snapshot = _snapshot(container)
            running = bool(((container.attrs or {}).get("State") or {}).get("Running"))

            if running and not force:
                raise BackendError(
                    ErrorResponse.Code.INVALID_STATE,
                    f"{name!r} is running; stop it first, or repeat with force to "
                    "kill and remove it in one step",
                )

            # v= is anonymous volumes only. Named volumes are never in scope:
            # they outlive the container and are shared with whatever else
            # mounts them.
            container.remove(force=force, v=remove_volumes)
            return snapshot, running
        except BackendError:
            raise
        except (APIError, DockerException) as exc:
            raise self._translate(exc, "remove", name) from exc

    def _act(self, name: str, verb: str, **kwargs) -> ContainerSnapshot:
        try:
            container = self.client.containers.get(name)
            getattr(container, verb)(**kwargs)
            # Re-read so the reply carries post-action state and the caller does
            # not have to follow every action with a list() and race the runtime.
            container.reload()
            return _snapshot(container)
        except (APIError, DockerException) as exc:
            raise self._translate(exc, verb, name) from exc

    @staticmethod
    def _translate(exc: Exception, what: str, name: str = "") -> BackendError:
        if isinstance(exc, NotFound):
            # Name the container, not the action: "no such container: read logs
            # for 'x'" is what the caller does NOT need to be told.
            return BackendError(
                ErrorResponse.Code.NOT_FOUND,
                f"no such container: {name}" if name else f"no such container ({what})",
            )
        if isinstance(exc, APIError):
            # The daemon answered, and said no.
            return BackendError(
                ErrorResponse.Code.IO_FAILURE, f"failed to {what}: {exc}"
            )
        # DockerException without an API response: the socket is gone, the
        # daemon is down, or we cannot open it at all.
        return BackendError(ErrorResponse.Code.UNAVAILABLE, f"failed to {what}: {exc}")


def _closer_for(frames) -> Callable[[], None]:
    """A callable that unblocks ``frames`` from another thread.

    A following ``logs()`` returns docker-py's ``CancellableStream``, whose
    ``close()`` reaches the underlying socket -- which is the only thing that
    ends a parked read, because streaming disables the socket timeout and the
    thread is never at a yield point for a generator ``close()`` to act on.

    The fallback covers the plain-generator shape (docker-py's streaming
    helpers close over a local named ``response``). If neither is available the
    close degrades to a no-op: follower threads are daemons, so shutdown still
    completes, it just stops being prompt -- not worth raising over.
    """

    def close() -> None:
        cancel = getattr(frames, "close", None)
        if callable(cancel):
            try:
                cancel()
                return
            except Exception:
                logger.debug("CancellableStream.close() failed", exc_info=True)

        response = getattr(getattr(frames, "gi_frame", None), "f_locals", {}).get(
            "response"
        )
        if response is None:
            logger.debug("No handle on the log stream; cannot close it early")
            return
        try:
            response.close()
        except Exception:
            logger.debug("Failed to close the log stream response", exc_info=True)

    return close


def snapshots_by_label(
    backend: DockerBackend, label: str
) -> Iterable[ContainerSnapshot]:
    """Adapter for :func:`selfid.resolve`'s ``list_by_label``."""
    return backend.list(label=label)
