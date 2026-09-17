"""The docker-py adapter: error translation and client reuse.

The `docker` module is not mocked wholesale -- only `docker.from_env` is
replaced, at its one construction site, so the real docker-py exception classes
are the ones being translated.
"""

import threading

import docker
import pytest
from docker.errors import APIError, DockerException, NotFound
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from container_control.backend import BackendError, DockerBackend, snapshots_by_label


class FakeImage:
    def __init__(self, tags=("img:1",), ident="sha256:abc"):
        self.tags = list(tags)
        self.id = ident


class FakeContainer:
    def __init__(self, name="app", ident="a" * 64, attrs=None, image=None, labels=None):
        self.name = name
        self.id = ident
        self.attrs = attrs if attrs is not None else {"State": {"Status": "running"}}
        self.image = image if image is not None else FakeImage()
        self.labels = labels or {}
        self.actions = []

    def reload(self):
        self.actions.append("reload")

    def start(self, **kwargs):
        self.actions.append(("start", kwargs))

    def stop(self, **kwargs):
        self.actions.append(("stop", kwargs))

    def restart(self, **kwargs):
        self.actions.append(("restart", kwargs))

    def remove(self, **kwargs):
        self.actions.append(("remove", kwargs))

    def logs(self, **kwargs):
        self.actions.append(("logs", kwargs))
        return b"2026-01-01T00:00:00Z line\n"


class FakeContainers:
    def __init__(self, containers, raises=None):
        self._containers = containers
        self._raises = raises
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if self._raises:
            raise self._raises
        return list(self._containers)

    def get(self, name_or_id):
        if self._raises:
            raise self._raises
        for c in self._containers:
            if name_or_id in (c.name, c.id):
                return c
        raise NotFound(f"no such container: {name_or_id}")


class FakeClient:
    created = 0

    def __init__(self, containers=(), raises=None, ping_raises=None):
        FakeClient.created += 1
        self.containers = FakeContainers(list(containers), raises)
        self._ping_raises = ping_raises

    def ping(self):
        if self._ping_raises:
            raise self._ping_raises
        return True


@pytest.fixture
def make_backend(monkeypatch):
    def build(**client_kwargs):
        FakeClient.created = 0
        client = FakeClient(**client_kwargs)
        monkeypatch.setattr(docker, "from_env", lambda **_: client)
        return DockerBackend(), client

    return build


class TestErrorTranslation:
    def test_not_found_becomes_not_found(self, make_backend):
        backend, _ = make_backend(containers=[FakeContainer()])
        with pytest.raises(BackendError) as excinfo:
            backend.get("nope")
        assert excinfo.value.code == ErrorResponse.Code.NOT_FOUND

    def test_api_error_becomes_io_failure(self, make_backend):
        # The daemon answered, and said no.
        backend, _ = make_backend(raises=APIError("conflict"))
        with pytest.raises(BackendError) as excinfo:
            backend.list()
        assert excinfo.value.code == ErrorResponse.Code.IO_FAILURE

    def test_docker_exception_becomes_unavailable(self, make_backend):
        # The socket is gone or the daemon is down.
        backend, _ = make_backend(raises=DockerException("connection refused"))
        with pytest.raises(BackendError) as excinfo:
            backend.list()
        assert excinfo.value.code == ErrorResponse.Code.UNAVAILABLE

    def test_ping_failure_is_unavailable(self, make_backend):
        backend, _ = make_backend(ping_raises=DockerException("permission denied"))
        with pytest.raises(BackendError) as excinfo:
            backend.ping()
        assert excinfo.value.code == ErrorResponse.Code.UNAVAILABLE

    def test_an_unopenable_socket_is_unavailable(self, monkeypatch):
        def boom(**_):
            raise DockerException("Error while fetching server API version")

        monkeypatch.setattr(docker, "from_env", boom)
        with pytest.raises(BackendError) as excinfo:
            DockerBackend().ping()
        assert excinfo.value.code == ErrorResponse.Code.UNAVAILABLE


class TestClientReuse:
    def test_one_client_per_thread_not_per_call(self, make_backend):
        backend, _ = make_backend(containers=[FakeContainer()])
        for _ in range(5):
            backend.list()
        # The old implementation called docker.from_env() on every one of these.
        assert FakeClient.created == 1

    def test_each_thread_gets_its_own(self, monkeypatch):
        FakeClient.created = 0
        monkeypatch.setattr(
            docker, "from_env", lambda **_: FakeClient(containers=[FakeContainer()])
        )
        backend = DockerBackend()
        threads = [threading.Thread(target=backend.list) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert FakeClient.created == 3


class TestSnapshots:
    def test_carries_name_id_tags_and_labels(self, make_backend):
        backend, _ = make_backend(
            containers=[FakeContainer(labels={"com.docker.compose.project": "slipway"})]
        )
        snap = backend.get("app")
        assert (snap.name, snap.id[:3]) == ("app", "aaa")
        assert snap.image_tags == ("img:1",)
        assert snap.labels["com.docker.compose.project"] == "slipway"

    def test_an_image_deleted_under_a_running_container_is_survivable(
        self, make_backend
    ):
        class Exploding(FakeContainer):
            @property
            def image(self):
                raise APIError("no such image")

            @image.setter
            def image(self, _value):
                pass  # the base __init__ assigns one; the getter is the point

        backend, _ = make_backend(containers=[Exploding()])
        # Dropping the row would be worse than reporting it without an image.
        assert backend.get("app").image_tags == ()

    def test_running_only_maps_to_the_all_flag(self, make_backend):
        backend, client = make_backend(containers=[FakeContainer()])
        backend.list(running_only=True)
        backend.list(running_only=False)
        assert [c["all"] for c in client.containers.list_calls] == [False, True]

    def test_label_filter_is_passed_through(self, make_backend):
        backend, client = make_backend(containers=[FakeContainer()])
        list(snapshots_by_label(backend, "keelson.container_control.self=1"))
        assert client.containers.list_calls[0]["filters"] == {
            "label": "keelson.container_control.self=1"
        }


class TestActions:
    def test_the_container_is_reloaded_so_the_reply_is_post_action(self, make_backend):
        container = FakeContainer()
        backend, _ = make_backend(containers=[container])
        backend.stop("app", timeout_s=7)
        assert ("stop", {"timeout": 7}) in container.actions
        assert "reload" in container.actions

    def test_start_takes_no_timeout(self, make_backend):
        container = FakeContainer()
        backend, _ = make_backend(containers=[container])
        backend.start("app", timeout_s=7)
        assert ("start", {}) in container.actions


#: A container the Engine reports as stopped. Module-level rather than a class
#: attribute: ruff's RUF012 rejects a mutable default on a class, and a shared
#: dict a test could mutate is exactly what that rule is about.
STOPPED_ATTRS = {"State": {"Status": "exited", "Running": False}}


class TestRemove:
    """The one action with nothing to re-read afterwards."""

    def test_a_stopped_container_is_removed_and_not_reloaded(self, make_backend):
        # reload() on a container that has just been deleted raises NotFound,
        # which would surface as "no such container" for a removal that in fact
        # succeeded.
        container = FakeContainer(attrs=STOPPED_ATTRS)
        backend, _ = make_backend(containers=[container])
        snapshot, was_running = backend.remove("app")
        assert ("remove", {"force": False, "v": False}) in container.actions
        assert "reload" not in container.actions
        assert (snapshot.name, was_running) == ("app", False)

    def test_a_running_container_is_refused_before_the_daemon_is_touched(
        self, make_backend
    ):
        container = FakeContainer(
            attrs={"State": {"Status": "running", "Running": True}}
        )
        backend, _ = make_backend(containers=[container])
        with pytest.raises(BackendError) as excinfo:
            backend.remove("app")
        assert excinfo.value.code == ErrorResponse.Code.INVALID_STATE
        assert not [a for a in container.actions if a[0] == "remove"]

    def test_force_removes_a_running_container_and_reports_that_it_was(
        self, make_backend
    ):
        container = FakeContainer(
            attrs={"State": {"Status": "running", "Running": True}}
        )
        backend, _ = make_backend(containers=[container])
        snapshot, was_running = backend.remove("app", force=True)
        assert ("remove", {"force": True, "v": False}) in container.actions
        assert was_running is True
        assert snapshot.id == "a" * 64

    def test_remove_volumes_maps_to_the_v_flag(self, make_backend):
        container = FakeContainer(attrs=STOPPED_ATTRS)
        backend, _ = make_backend(containers=[container])
        backend.remove("app", remove_volumes=True)
        assert ("remove", {"force": False, "v": True}) in container.actions

    def test_an_unknown_container_is_not_found(self, make_backend):
        backend, _ = make_backend(containers=[])
        with pytest.raises(BackendError) as excinfo:
            backend.remove("ghost")
        assert excinfo.value.code == ErrorResponse.Code.NOT_FOUND

    def test_a_daemon_refusal_still_translates(self, make_backend):
        # The daemon stays the authority: a container that starts between our
        # read and the delete gets its own 409, and it must not escape as a raw
        # docker-py exception.
        container = FakeContainer(attrs=STOPPED_ATTRS)

        def boom(**_):
            raise APIError("409 Client Error: Conflict")

        container.remove = boom
        backend, _ = make_backend(containers=[container])
        with pytest.raises(BackendError) as excinfo:
            backend.remove("app")
        assert excinfo.value.code == ErrorResponse.Code.IO_FAILURE


class TestLogs:
    def test_non_tty_reads_each_stream_separately(self, make_backend):
        container = FakeContainer(attrs={"Config": {"Tty": False}, "State": {}})
        backend, _ = make_backend(containers=[container])
        out, err, tty, _ = backend.logs("app", tail=10)
        assert not tty and out and err
        calls = [kwargs for verb, kwargs in container.actions if verb == "logs"]
        assert [(c["stdout"], c["stderr"]) for c in calls] == [
            (True, False),
            (False, True),
        ]

    def test_tty_reads_once_and_reports_it(self, make_backend):
        container = FakeContainer(attrs={"Config": {"Tty": True}, "State": {}})
        backend, _ = make_backend(containers=[container])
        out, err, tty, _ = backend.logs("app", tail=10)
        assert tty and out and err == b""

    def test_zero_tail_asks_for_everything(self, make_backend):
        container = FakeContainer(attrs={"Config": {"Tty": True}, "State": {}})
        backend, _ = make_backend(containers=[container])
        backend.logs("app", tail=0)
        assert next(k for v, k in container.actions if v == "logs")["tail"] == "all"

    def test_since_is_forwarded_only_when_given(self, make_backend):
        container = FakeContainer(attrs={"Config": {"Tty": True}, "State": {}})
        backend, _ = make_backend(containers=[container])
        backend.logs("app", tail=5)
        backend.logs("app", tail=5, since=1700000000)
        calls = [k for v, k in container.actions if v == "logs"]
        assert "since" not in calls[0] and calls[1]["since"] == 1700000000
