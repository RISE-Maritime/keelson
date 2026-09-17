"""End-to-end: readings out of a real Zenoh session and back through the SDK.

The round trip is the point. A key that looks right can still be undecodable
by a consumer, because foxglove and mcap do not trust the publisher -- they
parse the subject out of the key, look its schema up in the registry, and
decode against that. These tests do exactly what those consumers do.
"""

import json
import time

import pytest
import zenoh

import keelson
from pc.collectors import EMITTED_SUBJECTS, Reading, Sampler, collect_host_info
from pc.publishing import Publisher

pytestmark = pytest.mark.e2e

REALM = "test"
ENTITY = "pytest-host"


@pytest.fixture(name="session")
def fixture_session():
    """An isolated peer session: no multicast scouting, no endpoints, so the
    test cannot reach -- or be reached by -- a real bus on the same machine."""
    conf = zenoh.Config()
    conf.insert_json5("mode", json.dumps("peer"))
    conf.insert_json5("scouting/multicast/enabled", json.dumps(False))
    conf.insert_json5("listen/endpoints", json.dumps([]))
    conf.insert_json5("connect/endpoints", json.dumps([]))
    with zenoh.open(conf) as session:
        yield session


@pytest.fixture(name="collected")
def fixture_collected(session):
    """Subscribe to everything under the test entity and collect samples."""
    received = []
    session.declare_subscriber(
        f"{REALM}/@v0/{ENTITY}/pubsub/**",
        lambda sample: received.append(
            (str(sample.key_expr), bytes(sample.payload.to_bytes()))
        ),
    )
    time.sleep(0.3)  # let the subscriber declaration settle
    return received


def decode(key: str, envelope: bytes):
    """Do what a downstream consumer does: subject from the key, schema from
    the registry, payload decoded against that schema."""
    subject = keelson.get_subject_from_pubsub_key(key)
    _received_at, enclosed_at, payload = keelson.uncover(envelope)
    message = keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(subject)
    )
    return subject, enclosed_at, message


def test_readings_round_trip_through_the_bus(session, collected):
    publisher = Publisher(session, REALM, ENTITY, "pc")
    sent = [
        Reading("host_name", "", "nuc01"),
        Reading("host_boot_time", "", 1_700_000_000_000_000_000),
        Reading("cpu_load_pct", "", 42.5),
        Reading("cpu_temperature_celsius", "sensor/coretemp/package_id_0", 51.0),
        Reading("memory_used_pct", "", 75.0),
        Reading("swap_used_pct", "", 3.0),
        Reading("disk_used_pct", "disk/data", 91.25),
        Reading("disk_free_bytes", "disk/data", 4 * 2**40),
        Reading("network_interface_up", "net/eth0", True),
    ]
    assert {r.subject for r in sent} == EMITTED_SUBJECTS

    timestamp_ns = time.time_ns()
    assert publisher.publish(sent, timestamp_ns) == len(sent)

    time.sleep(0.7)
    assert len(collected) == len(sent)

    decoded = {}
    for key, envelope in collected:
        subject, enclosed_at, message = decode(key, envelope)
        decoded[subject] = message
        assert enclosed_at == timestamp_ns, "envelope must carry the sample time"

    assert decoded["host_name"].value == "nuc01"
    assert decoded["host_boot_time"].value.ToNanoseconds() == 1_700_000_000_000_000_000
    assert decoded["cpu_load_pct"].value == pytest.approx(42.5)
    assert decoded["cpu_temperature_celsius"].value == pytest.approx(51.0)
    assert decoded["disk_used_pct"].value == pytest.approx(91.25)
    assert decoded["disk_free_bytes"].value == 4 * 2**40
    assert decoded["network_interface_up"].value is True


def test_keys_follow_the_keelson_layout(session, collected):
    publisher = Publisher(session, REALM, ENTITY, "pc")
    publisher.publish(
        [
            Reading("cpu_load_pct", "", 1.0),
            Reading("disk_used_pct", "disk/data", 2.0),
            Reading("network_interface_up", "net/eth0", True),
        ]
    )
    time.sleep(0.7)

    keys = sorted(key for key, _ in collected)
    assert keys == [
        f"{REALM}/@v0/{ENTITY}/pubsub/cpu_load_pct/pc",
        f"{REALM}/@v0/{ENTITY}/pubsub/disk_used_pct/pc/disk/data",
        f"{REALM}/@v0/{ENTITY}/pubsub/network_interface_up/pc/net/eth0",
    ]
    for key in keys:
        parsed = keelson.parse_pubsub_key(key)
        assert parsed["entity_id"] == ENTITY
        assert parsed["source_id"].startswith("pc")


def test_publishers_are_declared_once_per_key(session):
    publisher = Publisher(session, REALM, ENTITY, "pc")
    reading = [Reading("cpu_load_pct", "", 1.0)]
    for _ in range(5):
        publisher.publish(reading)
    assert len(publisher.publishers) == 1
    publisher.undeclare()
    assert publisher.publishers == {}


def test_a_live_sample_reaches_the_bus_decodable(session, collected):
    """The real thing: sample this machine and confirm every key a consumer
    picks up resolves to a schema and decodes."""
    sampler = Sampler()
    sampler.prime()
    time.sleep(0.3)

    publisher = Publisher(session, REALM, ENTITY, "pc")
    readings = sampler.sample() + collect_host_info()
    assert readings, "sampling this host produced nothing at all"
    assert {r.subject for r in readings} <= EMITTED_SUBJECTS
    assert publisher.publish(readings) == len(readings)

    time.sleep(1.0)
    assert collected, "nothing arrived on the bus"

    for key, envelope in collected:
        decode(key, envelope)  # raises if the payload does not match the schema
