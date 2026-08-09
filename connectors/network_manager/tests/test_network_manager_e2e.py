"""End-to-end: a real zenoh session, a real responder, a real ping.

Exercises the parts the unit tests deliberately cannot — envelope round-trip,
protobuf field mapping, and the queryable/get plumbing — against a live
in-process peer session so no external router is required.
"""

import importlib.util
import pathlib
import sys
import time

import pytest

zenoh = pytest.importorskip("zenoh")

import keelson  # noqa: E402
from keelson.interfaces.NetworkPingPong_pb2 import (  # noqa: E402
    NetworkPing,
    NetworkPong,
)
from keelson.payloads.NetworkStatus_pb2 import NetworkStatus  # noqa: E402

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


class TestOverZenoh:
    """A declared queryable answering a real get."""

    def test_ping_a_live_responder(self, session):
        key = keelson.construct_rpc_key("test-realm", "roc-b", nm.PROCEDURE, "network")
        q = session.declare_queryable(key, nm._make_responder("network"))
        try:
            time.sleep(0.2)  # let the declaration propagate
            results = list(
                nm.ping_peer(
                    session,
                    realm="test-realm",
                    peer="roc-b",
                    self_entity="roc-a",
                    payload_bytes=0,
                    timeout_s=2.0,
                )
            )
            assert results, "responder did not answer"
            status = results[0]
            assert status.ping_host == "roc-a"
            assert status.pong_host == "roc-b"
            # Two processes on one host: sub-100 ms round trip, and the clocks
            # are literally the same clock, so skew must be negligible.
            assert 0 <= status.round_trip_time_ms < 100
            assert abs(status.clock_skew_ms) < 50
        finally:
            q.undeclare()

    def test_wildcard_responder_id_finds_any_source(self, session):
        """ping_peer wildcards the responder id, so an unusual source id still answers."""
        key = keelson.construct_rpc_key(
            "test-realm", "roc-c", nm.PROCEDURE, "some/odd/source"
        )
        q = session.declare_queryable(key, nm._make_responder("some/odd/source"))
        try:
            time.sleep(0.2)
            results = list(
                nm.ping_peer(session, "test-realm", "roc-c", "roc-a", 0, 2.0)
            )
            assert results, "wildcard responder id did not match"
        finally:
            q.undeclare()

    def test_silent_peer_yields_nothing_rather_than_a_zero(self, session):
        """Absence is the signal — a zero RTT would read as a perfect link."""
        results = list(
            nm.ping_peer(session, "test-realm", "nobody-home", "roc-a", 0, 0.5)
        )
        assert results == []

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
