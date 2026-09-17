"""Publishing resource utilisation: the sweep, the cache, and not dying.

The publisher's value is that a console can see what a host is costing without
shelling into it, so the things worth pinning are that a sample gets out every
tick -- the deliberate inverse of the status publisher's rule -- that the
previous-sample cache is keyed and reaped correctly, and, above both, that
nothing in here can take the RPC surface down.
"""

import threading
import time

import pytest

from keelson.payloads.ContainerHost_pb2 import ContainerHostStats
from container_control.stats_publisher import SUBJECT, ContainerStatsPublisher

from container_control_fakes import STOPPED_STATS_RAW, FakeBackend, snapshot, stats_raw

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


def _decode(payload: bytes) -> ContainerHostStats:
    import keelson

    _received, _enclosed, inner = keelson.uncover(payload)
    message = ContainerHostStats()
    message.ParseFromString(inner)
    return message


@pytest.fixture
def publisher_factory(monkeypatch):
    published = FakePublisher()
    monkeypatch.setattr(
        "container_control.stats_publisher.declare_publisher",
        lambda _session, _key: published,
    )

    made: list[ContainerStatsPublisher] = []

    def _make(backend, *, interval_s=0.02, globs=("*",)):
        pub = ContainerStatsPublisher(
            backend,
            FakeSession(),
            base_path="rise",
            entity_id="crab",
            source_id="big",
            globs=globs,
            interval_s=interval_s,
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


class TestSampling:
    def test_every_tick_publishes_because_every_tick_is_a_sample(
        self, publisher_factory
    ):
        # The deliberate inverse of the status publisher's
        # test_an_unchanged_tick_stays_quiet. Suppressing repeats there is what
        # lets a subscriber trust a message means something moved; suppressing
        # them here would put holes in a series and make a quiet container
        # indistinguishable from a stalled publisher.
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend)
        pub.start()

        assert _wait_for(lambda: published.count() >= 3)

    def test_the_sequence_advances_per_sample(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, published = publisher_factory(backend)
        pub.start()
        assert _wait_for(lambda: published.count() >= 2)

        assert _decode(published.puts[0]).sequence == 0
        assert _decode(published.puts[1]).sequence == 1

    def test_only_running_containers_are_sampled(self, publisher_factory):
        # Not a preference: a stopped container's stats call answers 200 with an
        # empty body, which would publish as an idle container.
        backend = FakeBackend(
            snapshots=[snapshot(name="up"), snapshot(name="down", status="exited")]
        )
        pub, published = publisher_factory(backend)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        assert [c.name for c in _decode(published.puts[-1]).containers] == ["up"]
        assert ("list", True, None) in backend.calls

    def test_a_container_that_stopped_mid_sweep_is_dropped_not_zeroed(
        self, publisher_factory
    ):
        # It was running at the listing and gone by its own stats call. The
        # Engine still answers, with nothing in it.
        backend = FakeBackend(
            snapshots=[snapshot(name="a"), snapshot(name="b", container_id="b" * 64)]
        )
        backend.stats_by_id["b" * 64] = STOPPED_STATS_RAW
        pub, published = publisher_factory(backend)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        assert [c.name for c in _decode(published.puts[-1]).containers] == ["a"]

    def test_the_glob_selects_containers(self, publisher_factory):
        backend = FakeBackend(
            snapshots=[snapshot(name="keelson-router"), snapshot(name="portainer")]
        )
        pub, published = publisher_factory(backend, globs=("keelson-*",))
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        assert [c.name for c in _decode(published.puts[-1]).containers] == [
            "keelson-router"
        ]


class TestTheSampleCache:
    def test_a_rate_appears_on_the_second_tick(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        backend.stats_by_id["a" * 64] = stats_raw(block_read=1000)
        pub, published = publisher_factory(backend, interval_s=0.05)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        first = _decode(published.puts[0]).containers[0]
        assert not first.HasField("block_read_bytes_per_second")

        backend.stats_by_id["a" * 64] = stats_raw(block_read=2000)
        assert _wait_for(
            lambda: any(
                c.HasField("block_read_bytes_per_second")
                for p in published.puts
                for c in _decode(p).containers
            )
        )

    def test_it_is_keyed_by_id_so_a_recreate_starts_over(self, publisher_factory):
        # `--force-recreate` reuses the NAME with counters back at zero.
        # Differencing across that would publish a spike that never happened,
        # so the id changing must read as "nothing to compare with".
        backend = FakeBackend(snapshots=[snapshot(name="a", container_id="1" * 64)])
        backend.stats_by_id["1" * 64] = stats_raw(block_read=9_000_000)
        pub, published = publisher_factory(backend, interval_s=0.05)
        pub.start()
        assert _wait_for(lambda: published.count() >= 2)

        backend.snapshots = [snapshot(name="a", container_id="2" * 64)]
        backend.stats_by_id["2" * 64] = stats_raw(block_read=10)
        seen = published.count()
        assert _wait_for(lambda: published.count() >= seen + 2)

        recreated = [
            c
            for payload in published.puts[seen:]
            for c in _decode(payload).containers
            if c.id == "2" * 64
        ]
        assert recreated
        assert not recreated[0].HasField("block_read_bytes_per_second")

    def test_it_is_reaped_by_being_rebuilt(self, publisher_factory):
        backend = FakeBackend(
            snapshots=[snapshot(name="a"), snapshot(name="b", container_id="b" * 64)]
        )
        pub, _published = publisher_factory(backend)
        pub.start()
        assert _wait_for(lambda: len(pub._previous) == 2)

        backend.snapshots = [snapshot(name="a")]
        # No separate reaper to forget: the sweep re-enumerates every tick, so
        # a container that is gone is simply absent from the next cache.
        assert _wait_for(lambda: len(pub._previous) == 1)

    def test_a_failed_listing_does_not_blank_every_rate_on_the_host(
        self, publisher_factory
    ):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, _published = publisher_factory(backend, interval_s=0.05)
        pub.start()
        assert _wait_for(lambda: len(pub._previous) == 1)

        backend.raises = RuntimeError("daemon bounced")
        time.sleep(0.2)
        # Kept, not cleared: the next good tick still has something to
        # difference against.
        assert len(pub._previous) == 1


class TestResilience:
    def test_one_containers_failure_does_not_cost_the_others_their_sample(
        self, publisher_factory
    ):
        backend = FakeBackend(
            snapshots=[snapshot(name="a"), snapshot(name="b", container_id="b" * 64)]
        )
        backend.stats_raises["b" * 64] = RuntimeError("gone")
        pub, published = publisher_factory(backend)
        pub.start()
        assert _wait_for(lambda: published.count() >= 1)

        assert [c.name for c in _decode(published.puts[-1]).containers] == ["a"]

    def test_a_raising_backend_does_not_kill_the_thread(self, publisher_factory):
        backend = FakeBackend(
            snapshots=[snapshot(name="a")], raises=RuntimeError("boom")
        )
        pub, published = publisher_factory(backend)
        pub.start()
        time.sleep(0.15)
        assert published.count() == 0

        backend.raises = None
        # The primary job of this process is answering list/start/stop; a
        # daemon hiccup must cost a sample, not the responder.
        assert _wait_for(lambda: published.count() >= 1)

    def test_stop_joins_the_thread(self, publisher_factory):
        backend = FakeBackend(snapshots=[snapshot(name="a")])
        pub, _published = publisher_factory(backend)
        pub.start()
        pub.stop()

        assert pub._thread is None


def test_the_subject_is_the_registered_one():
    assert SUBJECT == "container_stats"


def test_the_pubsub_key_is_one_per_host(publisher_factory, monkeypatch):
    import keelson

    keys = []
    monkeypatch.setattr(
        "container_control.stats_publisher.declare_publisher",
        lambda _session, key: keys.append(key) or FakePublisher(),
    )
    pub = ContainerStatsPublisher(
        FakeBackend(),
        FakeSession(),
        base_path="rise",
        entity_id="crab",
        source_id="big",
    )
    pub.start()
    pub.stop()

    assert keys == [keelson.construct_pubsub_key("rise", "crab", SUBJECT, "big")]
