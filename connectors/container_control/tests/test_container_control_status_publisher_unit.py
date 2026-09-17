"""Publishing container state: change detection, the heartbeat, and not dying.

The publisher's whole value is that a console can stop polling, so the two
things worth pinning are that a change gets out promptly and that an unchanged
tick stays quiet -- and, above both, that no failure in here can take the RPC
surface down with it.
"""

import threading
import time

import pytest

from container_control.guard import ControlGuard
from keelson.payloads.ContainerHost_pb2 import (
    ContainerHostStatus,
    ContainerStatusTrigger,
)
from container_control.status_publisher import SUBJECT, ContainerStatusPublisher

from container_control_fakes import FakeBackend, snapshot

pytestmark = pytest.mark.unit


class FakePublisher:
    def __init__(self):
        self.puts: list[bytes] = []
        self.lock = threading.Lock()

    def put(self, payload):
        with self.lock:
            self.puts.append(bytes(payload))

    def count(self) -> int:
        with self.lock:
            return len(self.puts)


class FakeSession:
    """`declare_publisher` is imported into the module under test, so the
    session only has to be an object the monkeypatched helper ignores."""


def _decode(payload: bytes) -> ContainerHostStatus:
    import keelson

    _received, _enclosed, inner = keelson.uncover(payload)
    message = ContainerHostStatus()
    message.ParseFromString(inner)
    return message


@pytest.fixture
def publisher_factory(monkeypatch):
    published = FakePublisher()
    monkeypatch.setattr(
        "container_control.status_publisher.declare_publisher",
        lambda _session, _key: published,
    )

    made: list[ContainerStatusPublisher] = []

    def _make(
        backend,
        *,
        interval_s=0.02,
        heartbeat_s=10.0,
        control=False,
        expose_env_values=False,
    ):
        pub = ContainerStatusPublisher(
            backend,
            ControlGuard(
                control_enabled=control, allow_globs=("*",) if control else ()
            ),
            FakeSession(),
            base_path="rise",
            entity_id="crab",
            source_id="big",
            interval_s=interval_s,
            heartbeat_s=heartbeat_s,
            expose_env_values=expose_env_values,
        )
        made.append(pub)
        return pub, published

    yield _make

    for pub in made:
        pub.stop()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestChangeDetection:
    def test_the_first_tick_publishes(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        message = _decode(published.puts[0])
        assert [c.name for c in message.containers] == ["a"]
        # Nothing was known before, so the first sample is a change, not a
        # keep-alive -- a console must not read it as "nothing happened".
        assert message.trigger == ContainerStatusTrigger.CONTAINER_STATUS_TRIGGER_CHANGE
        assert message.sequence == 0

    def test_an_unchanged_tick_stays_quiet(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend, heartbeat_s=1000.0)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        first = published.count()
        time.sleep(0.3)  # many ticks at interval_s=0.02
        # The whole point: polling would have sent a dozen identical answers.
        assert published.count() == first

    def test_a_changed_set_publishes_again(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend, heartbeat_s=1000.0)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        backend.snapshots.append(snapshot(name="b"))
        assert _wait_for(lambda: published.count() >= 2)

        message = _decode(published.puts[-1])
        assert sorted(c.name for c in message.containers) == ["a", "b"]
        assert message.trigger == ContainerStatusTrigger.CONTAINER_STATUS_TRIGGER_CHANGE

    def test_a_removed_container_is_a_change(self, publisher_factory):
        # Removal by omission is the reason this subject is one key per host.
        backend = FakeBackend(snapshots=[snapshot(name="a"), snapshot(name="b")])
        pub, published = publisher_factory(backend, heartbeat_s=1000.0)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        backend.snapshots.pop()
        assert _wait_for(lambda: published.count() >= 2)
        assert [c.name for c in _decode(published.puts[-1]).containers] == ["a"]

    def test_sequence_increments_per_sample(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend, heartbeat_s=1000.0)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)
        backend.snapshots.append(snapshot(name="b"))
        assert _wait_for(lambda: published.count() >= 2)

        assert [_decode(p).sequence for p in published.puts[:2]] == [0, 1]


class TestHeartbeat:
    def test_unchanged_state_is_republished_when_due(self, publisher_factory):
        # Zenoh does not backfill, so a subscriber joining during a quiet period
        # would otherwise wait indefinitely for its first value.
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend, heartbeat_s=0.05)
        pub.start()
        assert _wait_for(lambda: published.count() >= 3)

        triggers = [_decode(p).trigger for p in published.puts[:3]]
        assert triggers[0] == ContainerStatusTrigger.CONTAINER_STATUS_TRIGGER_CHANGE
        # ...and the repeats say they are repeats, so "last change" and "last
        # heard" stay distinguishable at the consumer.
        assert (
            triggers[1:]
            == [ContainerStatusTrigger.CONTAINER_STATUS_TRIGGER_HEARTBEAT] * 2
        )


class TestItNeverTakesTheRpcSurfaceDown:
    def test_a_raising_backend_does_not_kill_the_thread(self, publisher_factory):
        backend = FakeBackend(
            snapshots=[snapshot(name="a")], raises=RuntimeError("daemon gone")
        )
        pub, published = publisher_factory(backend)
        pub.start()
        time.sleep(0.2)

        assert published.count() == 0
        assert pub._thread is not None and pub._thread.is_alive()

        # ...and it recovers on its own when the daemon comes back, rather than
        # needing the process restarted.
        backend.raises = None
        assert _wait_for(lambda: published.count() >= 1)

    def test_stop_joins_the_thread(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, _published = publisher_factory(backend)
        pub.start()
        thread = pub._thread
        assert _wait_for(lambda: thread.is_alive())

        pub.stop()
        assert not thread.is_alive()
        assert pub._thread is None


class TestPayload:
    def test_it_carries_the_control_flag_and_a_timestamp(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend, control=True)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        message = _decode(published.puts[0])
        assert message.control_enabled is True
        assert message.HasField("observed_at")

    def test_the_subject_is_the_registered_one(self):
        assert SUBJECT == "container_status"


class TestDeploymentDetail:
    @staticmethod
    def _backend():
        snap = snapshot(name="a")
        snap.attrs["Config"]["Env"] = ["API_TOKEN=hunter2"]
        return FakeBackend(snapshots=[snap])

    def test_env_values_are_withheld_on_the_published_subject_by_default(
        self, publisher_factory
    ):
        pub, published = publisher_factory(self._backend())
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)
        (container,) = _decode(published.puts[0]).containers
        assert [v.name for v in container.env] == ["API_TOKEN"]
        assert not container.env[0].HasField("value")

    def test_the_flag_reaches_the_published_subject(self, publisher_factory):
        pub, published = publisher_factory(self._backend(), expose_env_values=True)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)
        (container,) = _decode(published.puts[0]).containers
        assert container.env[0].value == "hunter2"
