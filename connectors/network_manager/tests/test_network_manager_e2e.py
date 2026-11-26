"""End-to-end: a real zenoh session, a real responder, a real ping.

Exercises the parts the unit tests deliberately cannot — envelope round-trip,
protobuf field mapping, and the queryable/get plumbing — against a live
in-process peer session so no external router is required.
"""

import argparse
import importlib.util
import pathlib
import sys
import threading
import time

import pytest

zenoh = pytest.importorskip("zenoh")

import keelson  # noqa: E402
from keelson.interfaces.NetworkPingPong_pb2 import (  # noqa: E402
    NetworkPing,
    NetworkPong,
)
from keelson.payloads.NetworkStatus_pb2 import NetworkStatus  # noqa: E402
from keelson.payloads.Primitives_pb2 import TimestampedFloatPeriod  # noqa: E402

pytestmark = pytest.mark.e2e

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "network_manager2keelson.py"


def _load_module():
    """Load the bin script by path — it has no .py-importable package name."""
    spec = importlib.util.spec_from_file_location("network_manager2keelson", BIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


nm = _load_module()


def _undeclare(server) -> None:
    """Tear down what nm.serve() declared so tests don't leak tokens."""
    if server.liveliness_token is not None:
        server.liveliness_token.undeclare()
    for q in server.queryables:
        q.undeclare()


@pytest.fixture(scope="module")
def session():
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    # Multicast scouting only, so the test never touches a real router.
    conf.insert_json5("connect/endpoints", "[]")
    with zenoh.open(conf) as s:
        yield s


class TestPongContract:
    """build_pong / build_status without any transport in the way."""

    def test_round_trip_recovers_the_timestamps(self):
        ping = NetworkPing()
        t1 = time.time_ns()
        ping.sent_at.FromNanoseconds(t1)
        request = keelson.enclose(ping.SerializeToString())

        # A real receive time from the same clock the responder stamps t3 with.
        # Fabricating a future t2 makes t3 < t2, which cannot happen in
        # practice — both come from the responder's own clock.
        t2 = time.time_ns()
        reply = nm.build_pong(request, t2)

        _, _, content = keelson.uncover(reply)
        pong = NetworkPong.FromString(content)
        assert pong.ping.sent_at.ToNanoseconds() == t1
        assert pong.ping_received_at.ToNanoseconds() == t2
        # t3 is stamped by the responder at reply time, so it must be >= t2.
        assert pong.sent_at.ToNanoseconds() >= t2

    def test_status_carries_both_ends_and_real_numbers(self):
        ping = NetworkPing()
        t1 = time.time_ns()
        ping.sent_at.FromNanoseconds(t1)
        request = keelson.enclose(ping.SerializeToString())

        t2 = t1 + 5_000_000
        reply = nm.build_pong(request, t2)
        t4 = t2 + 10_000_000

        status = nm.build_status(reply, t4, "roc-a", "roc-b")
        assert status.ping_host == "roc-a"
        assert status.pong_host == "roc-b"
        assert status.round_trip_time_ms > 0
        assert status.latency_ms == pytest.approx(status.round_trip_time_ms / 2)

    def test_payload_size_survives_the_round_trip(self):
        ping = NetworkPing()
        ping.sent_at.FromNanoseconds(time.time_ns())
        ping.payload = b"\0" * (1024 * 1024)
        request = keelson.enclose(ping.SerializeToString())
        reply = nm.build_pong(request, time.time_ns())
        status = nm.build_status(reply, time.time_ns(), "a", "b")
        assert status.payload_size_mb == pytest.approx(1.0, abs=0.01)


def _period(payload: bytes) -> TimestampedFloatPeriod:
    _, _, content = keelson.uncover(payload)
    return TimestampedFloatPeriod.FromString(content)


def _status():
    ping = NetworkPing()
    ping.sent_at.FromNanoseconds(time.time_ns())
    reply = nm.build_pong(keelson.enclose(ping.SerializeToString()), time.time_ns())
    return nm.build_status(reply, time.time_ns(), "roc-a", "roc-b")


def _args(**kw):
    base = dict(
        realm="test-realm",
        entity_id="roc-a",
        source_id="network",
        peers="",
        interval=0.05,
        timeout=0.1,
        window=60.0,
        payload_bytes=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


class TestWindowedSubjects:
    """The link over time, as its own subjects, every round (#314 review)."""

    def test_network_status_stays_one_exchange(self):
        """No window fields: a status is one exchange, the window is a subject."""
        names = {f.name for f in NetworkStatus.DESCRIPTOR.fields}
        assert not names & {"jitter_ms", "pings_sent", "pongs_received", "window_s"}

    def test_period_payload_round_trips(self):
        msg = _period(nm.build_period(12.5, 1_700_000_000_000_000_000, 42.0))
        assert msg.value == pytest.approx(12.5)
        assert msg.timestamp.ToNanoseconds() == 1_700_000_000_000_000_000
        assert msg.period.ToNanoseconds() == 42_000_000_000

    @pytest.mark.parametrize(
        "subject", [nm.LOSS_SUBJECT, nm.JITTER_SUBJECT, nm.P95_SUBJECT]
    )
    def test_subjects_are_registered_as_windowed_floats(self, subject):
        assert keelson.get_subject_schema(subject) == "keelson.TimestampedFloatPeriod"

    def test_responder_keeps_every_chunk_of_its_id(self):
        key = keelson.construct_rpc_key(
            "rise",
            "shore",
            nm.INTERFACE,
            nm.VERSION,
            nm.PROCEDURE,
            "srv-herakles/sjofartsverket",
        )
        assert nm.responder_of(key, "rise", "shore") == "srv-herakles/sjofartsverket"

    def _round(self, puts, peer_window, links, answers, at, answered=None, wall=0):
        nm._publish_round(
            lambda subj, src, payload: puts.append((subj, src, payload)),
            _args(),
            "shore",
            peer_window,
            links,
            nm.PingRound(bool(answers) if answered is None else answered, answers),
            at,
            end_s=at + 0.1,
            end_ns=wall,
        )

    def _periods(self, puts, subject):
        return {
            src: _period(payload).period.ToNanoseconds() / 1e9
            for subj, src, payload in puts
            if subj == subject
        }

    def test_timestamp_is_wall_time_and_the_window_is_not(self):
        """A wall clock that steps back an hour must not dilute an outage."""
        puts, pw, links = [], nm.PeerWindow(60), {}
        hour = 3600 * 10**9
        for i in range(6):
            self._round(
                puts, pw, links, [("a", _status())], i * 12.0, wall=2 * hour + i
            )
        puts.clear()
        for i in range(6, 14):
            self._round(puts, pw, links, [], i * 12.0, wall=hour + i)
        losses = [p for p in puts if p[0] == nm.LOSS_SUBJECT]
        last = _period(losses[-1][2])
        assert last.value == pytest.approx(100.0)
        assert last.timestamp.ToNanoseconds() == hour + 13
        assert not links  # the silent link has aged out with its jitter and p95

    def test_jitter_and_p95_cover_the_links_own_span(self):
        """A link back after an outage has two answers over 12 s, not over the
        peer's whole window."""
        puts, pw, links = [], nm.PeerWindow(60), {}
        for i in range(4):
            self._round(puts, pw, links, [], i * 12.0)
        self._round(puts, pw, links, [("a", _status())], 48.0)
        puts.clear()
        self._round(puts, pw, links, [("a", _status())], 60.0)
        assert self._periods(puts, nm.LOSS_SUBJECT) == {
            "network/shore": pytest.approx(48.1, abs=0.1)
        }
        for subject in (nm.JITTER_SUBJECT, nm.P95_SUBJECT):
            assert self._periods(puts, subject) == {
                "network/shore/a": pytest.approx(12.1, abs=0.01)
            }

    def test_an_error_reply_is_not_loss(self):
        puts, pw, links = [], nm.PeerWindow(60), {}
        self._round(puts, pw, links, [], 100.0, answered=True)
        assert [p[0] for p in puts] == [nm.LOSS_SUBJECT]
        assert _period(puts[0][2]).value == pytest.approx(0.0)

    def test_only_a_responders_error_counts_as_an_answer(self):
        """zenoh reports a query nobody answered in time as an error, too."""
        error = nm.ErrorResponse(error_description="no").SerializeToString()
        assert nm.is_responder_error(error)
        assert not nm.is_responder_error(b"Timeout")
        assert not nm.is_responder_error(b"")

    @pytest.mark.parametrize(
        "peers, window, ok",
        [
            ("", 0.1, True),  # answers pings only: the window is not used
            ("a", 300.0, True),
            ("a", 14.0, False),  # two rounds of 12 s, the second 2 s long
            ("a", 14.1, True),
            ("a,b,c,d,e", 22.0, False),
            ("a", 2.0, False),  # not even one round
        ],
    )
    def test_a_window_that_cannot_hold_two_rounds_is_warned_about(
        self, peers, window, ok
    ):
        args = _args(peers=peers, window=window, interval=10.0, timeout=2.0)
        assert (nm.window_warning(args) is None) is ok

    def test_a_ping_survives_a_wall_clock_step(self, monkeypatch):
        """The clock steps back an hour once the ping is sent, and again during
        the wait: the wait still ends at its timeout, and the round trip is the
        30 ms it took."""

        class Clock:
            mono_ns, step_ns, slept = 0, 0, 0

            def monotonic(self):
                return self.mono_ns / 1e9

            def monotonic_ns(self):
                return self.mono_ns

            def time(self):
                return self.time_ns() / 1e9

            def time_ns(self):
                return 10_000 * 10**9 + self.mono_ns + self.step_ns

            def sleep(self, seconds):
                self.slept += 1
                assert self.slept < 1000, "the wait never ended"
                self.mono_ns += int(seconds * 1e9)
                self.step_ns = -7200 * 10**9

        clock = Clock()
        monkeypatch.setattr(nm, "time", clock)
        key = keelson.construct_rpc_key(
            "r", "shore", nm.INTERFACE, nm.VERSION, nm.PROCEDURE, "a"
        )

        class Session:
            def get(self, _, callback, payload):
                pong = nm.build_pong(payload, clock.time_ns())
                clock.step_ns = -3600 * 10**9
                clock.mono_ns += 30 * 10**6
                ok = argparse.Namespace(key_expr=key, payload=pong)
                callback(argparse.Namespace(ok=ok))

        answered, answers = nm.ping_peer(Session(), "r", "shore", "me", 0, 2.0)
        assert answered
        assert answers[0][1].round_trip_time_ms == pytest.approx(30.0)

    def test_the_loop_survives_a_wall_clock_step(self, monkeypatch):
        """The loop itself, on a fake clock: six answered rounds, the wall clock
        steps back an hour, the link drops, and loss reaches 100 %."""

        class Clock:
            mono, step = 0.0, 0.0

            def monotonic(self):
                return self.mono

            def time(self):
                return 10_000.0 + self.mono + self.step

            def time_ns(self):
                return int(self.time() * 1e9)

            def sleep(self, seconds):
                self.mono += seconds

        clock, puts, rounds = Clock(), [], []

        def ping_peer(*_):
            rounds.append(None)
            clock.sleep(2.0)
            if len(rounds) <= 6:
                return nm.PingRound(True, [("a", _status())])
            clock.step = -3600.0
            if len(rounds) > 14:
                raise KeyboardInterrupt
            return nm.PingRound(False, [])

        class Publisher:
            def __init__(self, key):
                self.key = key

            def put(self, payload):
                puts.append((self.key, payload))

            def undeclare(self):
                pass

        monkeypatch.setattr(nm, "time", clock)
        monkeypatch.setattr(nm, "ping_peer", ping_peer)
        monkeypatch.setattr(nm, "declare_publisher", lambda _, key: Publisher(key))
        nm._measure_forever(None, _args(interval=10.0), ["shore"])
        last = _period([p for k, p in puts if nm.LOSS_SUBJECT in k][-1])
        assert last.value == pytest.approx(100.0)
        assert last.timestamp.ToNanoseconds() < 10_000 * 10**9  # still wall time

    def test_a_silent_round_still_publishes_loss(self):
        """Review point 1: a total outage is loss at 100 %, not silence."""
        puts, pw, links = [], nm.PeerWindow(60), {}
        self._round(puts, pw, links, [], 100.0)
        self._round(puts, pw, links, [], 110.0)
        losses = [p for p in puts if p[0] == nm.LOSS_SUBJECT]
        assert [src for _, src, _ in losses] == ["network/shore", "network/shore"]
        assert _period(losses[-1][2]).value == pytest.approx(100.0)
        assert not [p for p in puts if p[0] != nm.LOSS_SUBJECT]

    def test_period_is_the_span_covered_from_startup(self):
        """Review point 3: ten seconds in, the period says ten seconds."""
        puts, pw, links = [], nm.PeerWindow(60), {}
        self._round(puts, pw, links, [], 100.0)
        self._round(puts, pw, links, [], 110.0)
        period = _period(puts[-1][2]).period.ToNanoseconds() / 1e9
        assert period == pytest.approx(10.1)

    def test_jitter_and_p95_are_keyed_per_link_once_two_answers_are_in(self):
        puts, pw, links = [], nm.PeerWindow(60), {}
        self._round(puts, pw, links, [("netman", _status())], 100.0)
        assert {p[0] for p in puts} == {nm.SUBJECT, nm.LOSS_SUBJECT}
        puts.clear()
        self._round(puts, pw, links, [("netman", _status())], 110.0)
        by_subject = {subj: src for subj, src, _ in puts}
        assert by_subject[nm.LOSS_SUBJECT] == "network/shore"
        assert by_subject[nm.JITTER_SUBJECT] == "network/shore/netman"
        assert by_subject[nm.P95_SUBJECT] == "network/shore/netman"

    def test_two_responders_are_two_links(self):
        puts, pw, links = [], nm.PeerWindow(60), {}
        both = [("a", _status()), ("b/two", _status())]
        self._round(puts, pw, links, both, 100.0)
        self._round(puts, pw, links, both, 110.0)
        jitter_keys = sorted(src for subj, src, _ in puts if subj == nm.JITTER_SUBJECT)
        assert jitter_keys == ["network/shore/a", "network/shore/b/two"]
        loss = [p for p in puts if p[0] == nm.LOSS_SUBJECT]
        assert _period(loss[-1][2]).value == pytest.approx(0.0)


class TestOverZenoh:
    """A declared queryable answering a real get."""

    def test_ping_a_live_responder(self, session):
        server = nm.serve(session, "test-realm", "roc-b", "network")
        try:
            time.sleep(0.2)  # let the declaration propagate
            answered, results = nm.ping_peer(
                session,
                realm="test-realm",
                peer="roc-b",
                self_entity="roc-a",
                payload_bytes=0,
                timeout_s=2.0,
            )
            assert answered
            assert results, "responder did not answer"
            responder, status = results[0]
            assert responder == "network"
            assert status.ping_host == "roc-a"
            assert status.pong_host == "roc-b"
            # Two processes on one host: sub-100 ms round trip, and the clocks
            # are literally the same clock, so skew must be negligible.
            assert 0 <= status.round_trip_time_ms < 100
            assert abs(status.clock_skew_ms) < 50
        finally:
            _undeclare(server)

    def test_wildcard_responder_id_finds_any_source(self, session):
        """ping_peer wildcards the responder id, so an unusual source id still answers."""
        server = nm.serve(session, "test-realm", "roc-c", "some/odd/source")
        try:
            time.sleep(0.2)
            _, results = nm.ping_peer(session, "test-realm", "roc-c", "roc-a", 0, 2.0)
            assert results, "wildcard responder id did not match"
            assert results[0][0] == "some/odd/source"
        finally:
            _undeclare(server)

    def test_responder_advertises_its_interface_over_liveliness(self, session):
        """The link-health connector must itself be discoverable (#234)."""
        server = nm.serve(session, "test-realm", "roc-d", "network")
        try:
            time.sleep(0.2)
            token_key = keelson.construct_rpc_interface_liveliness_key(
                "test-realm", "roc-d", nm.INTERFACE, nm.VERSION, "network"
            )
            alive = [str(r.ok.key_expr) for r in session.liveliness().get(token_key)]
            assert alive == [token_key]
        finally:
            _undeclare(server)

    def test_source_and_subject_tokens_are_declared_by_run(self, session):
        """run() must hold the source-level and network_status tokens (#234)."""
        args = _args(entity_id="roc-e")
        expected = {
            "test-realm/@v0/roc-e/*/network",
            *(f"test-realm/@v0/roc-e/pubsub/{s}/network" for s in nm.SUBJECTS),
            f"test-realm/@v0/roc-e/@rpc/{nm.INTERFACE}/{nm.VERSION}/*/network",
        }
        t = threading.Thread(target=nm.run, args=(session, args), daemon=True)
        t.start()
        try:
            # Queried one by one: `**` never crosses the verbatim `@rpc` chunk,
            # so a single wildcard query would miss the interface token.
            deadline = time.time() + 2
            alive = set()
            while time.time() < deadline and alive != expected:
                alive = {
                    str(r.ok.key_expr)
                    for key in expected
                    for r in session.liveliness().get(key)
                }
                time.sleep(0.05)
            assert alive == expected
        finally:
            pass  # daemon thread; the module-scoped session closes it down

    def test_an_unreachable_peer_reads_as_loss_on_the_bus(self, session):
        """Review point 1, end to end: a peer that never answers keeps producing
        network_ping_loss_pct = 100 every round, keyed by the peer."""
        received = []
        key = keelson.construct_pubsub_key(
            "test-realm", "roc-f", nm.LOSS_SUBJECT, "network/nobody-home"
        )
        sub = session.declare_subscriber(
            key, lambda s: received.append(bytes(s.payload))
        )
        args = _args(entity_id="roc-f", peers="nobody-home")
        t = threading.Thread(target=nm.run, args=(session, args), daemon=True)
        t.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and len(received) < 2:
                time.sleep(0.05)
            assert len(received) >= 2, "loss was not published every round"
            assert _period(received[-1]).value == pytest.approx(100.0)
        finally:
            sub.undeclare()

    def test_silent_peer_yields_nothing_rather_than_a_zero(self, session):
        """Absence is the signal — a zero RTT would read as a perfect link."""
        answered, results = nm.ping_peer(
            session, "test-realm", "nobody-home", "roc-a", 0, 0.5
        )
        assert results == []
        assert not answered

    def test_an_error_reply_is_an_answer_without_a_status(self, session):
        key = keelson.construct_rpc_key(
            "test-realm", "roc-g", nm.INTERFACE, nm.VERSION, nm.PROCEDURE, "network"
        )
        error = nm.ErrorResponse(error_description="no").SerializeToString()
        queryable = session.declare_queryable(key, lambda q: q.reply_err(error))
        try:
            time.sleep(0.2)
            assert nm.ping_peer(session, "test-realm", "roc-g", "roc-a", 0, 0.5) == (
                True,
                [],
            )
        finally:
            queryable.undeclare()

    def test_published_status_decodes_as_the_canonical_subject(self, session):
        """What a consumer actually sees on the bus."""
        received = []
        pub_key = keelson.construct_pubsub_key(
            "test-realm", "roc-a", nm.SUBJECT, "network"
        )
        sub = session.declare_subscriber(
            pub_key, lambda s: received.append(bytes(s.payload))
        )
        pub = session.declare_publisher(pub_key)
        try:
            time.sleep(0.2)
            ping = NetworkPing()
            ping.sent_at.FromNanoseconds(time.time_ns())
            reply = nm.build_pong(
                keelson.enclose(ping.SerializeToString()), time.time_ns()
            )
            status = nm.build_status(reply, time.time_ns(), "roc-a", "roc-b")
            pub.put(keelson.enclose(status.SerializeToString()))

            deadline = time.time() + 2
            while time.time() < deadline and not received:
                time.sleep(0.05)
            assert received, "nothing arrived on the network_status subject"

            _, _, content = keelson.uncover(received[0])
            decoded = NetworkStatus.FromString(content)
            assert decoded.ping_host == "roc-a"
            assert decoded.pong_host == "roc-b"
        finally:
            sub.undeclare()
            pub.undeclare()


def test_subject_is_registered_in_keelson():
    """A subject nothing can resolve is a subject nothing can decode."""
    assert keelson.get_subject_schema(nm.SUBJECT) == "keelson.NetworkStatus"
