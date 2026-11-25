"""End-to-end: the gate as a subprocess on a real Zenoh peer mesh.

This process plays the vessel's sensors and the ROC: it publishes her fix,
course and speed, and a handover record, and reads back `mandate_gate`.
"""

import json
import threading
import time

import pytest
import zenoh

from keelson import (
    construct_pubsub_key,
    construct_source_liveliness_key,
    enclose,
    uncover,
)
from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.MandateGate_pb2 import MandateGate
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.scaffolding import create_zenoh_config

from mandate_gate.geometry import Plane

REALM = "test-realm"
ENTITY_ID = "test-vessel"
CHECKLIST_REALM = "test-roc"
CHECKLIST_ENTITY = "roc-under-test"

LAT0, LON0 = 58.0, 10.9
SECTOR = [[57.97, 11.0], [58.03, 11.0], [58.03, 11.1], [57.97, 11.1]]
EDGE_M = Plane(LAT0, LON0).to_en(LAT0, 11.0)[0]


class _Gate:
    """The newest mandate_gate sample."""

    def __init__(self):
        self.latest = None
        self.lock = threading.Lock()

    def __call__(self, sample):
        _, _, payload = uncover(bytes(sample.payload.to_bytes()))
        msg = MandateGate()
        msg.ParseFromString(payload)
        with self.lock:
            self.latest = msg

    def wait_for(self, predicate, timeout, feed=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if feed is not None:
                feed()
            with self.lock:
                if self.latest is not None and predicate(self.latest):
                    return self.latest
            time.sleep(0.2)
        raise AssertionError(f"mandate_gate never got there; last: {self.latest}")


@pytest.fixture
def bus(zenoh_endpoints):
    session = zenoh.open(
        create_zenoh_config(
            mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
        )
    )
    try:
        yield session
    finally:
        session.close()


def start_gate(factory, zenoh_endpoints, tmp_path):
    config = tmp_path / "gate.json"
    config.write_text(
        json.dumps(
            {
                "initial_watch": "ROC-1",
                "areas": [
                    {"id": "east-sector", "roc_sites": ["ROC-2"], "polygon": SECTOR}
                ],
                "margin_m": 200,
                "loiter_radius_m": 400,
                "stopping": {"speed_knots": 11, "distance_m": 1088},
            }
        )
    )
    gate = factory(
        "mandate_gate",
        "mandate-gate2keelson.py",
        [
            "--realm",
            REALM,
            "--entity-id",
            ENTITY_ID,
            "--checklist-realm",
            CHECKLIST_REALM,
            "--checklist-entity",
            CHECKLIST_ENTITY,
            "--connect",
            zenoh_endpoints["connect"],
            "--config",
            str(config),
            "--publish-interval-s",
            "0.5",
        ],
    )
    gate.start()
    return gate


def wait_until_present(session, timeout=20.0):
    key = construct_source_liveliness_key(REALM, ENTITY_ID, "mandate-gate")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for reply in session.liveliness().get(key, timeout=1.0):
            if reply.ok is not None:
                return
        time.sleep(0.2)
    raise AssertionError(f"the gate never became present at {key}")


def sensors(session, east_m, cog=90.0, sog=11.0):
    lat, lon = Plane(LAT0, LON0).to_latlon(east_m, 0.0)

    def feed():
        fix = LocationFix(latitude=lat, longitude=lon)
        session.put(
            construct_pubsub_key(REALM, ENTITY_ID, "location_fix", "gnss/0"),
            enclose(fix.SerializeToString()),
        )
        for subject, value in (
            ("course_over_ground_deg", cog),
            ("speed_over_ground_knots", sog),
        ):
            reading = TimestampedFloat(value=value)
            session.put(
                construct_pubsub_key(REALM, ENTITY_ID, subject, "gnss/0"),
                enclose(reading.SerializeToString()),
            )

    return feed


def handover_to(site, handover_id="h-1"):
    h = ChecklistHandover(
        handover_id=handover_id, status=ChecklistHandover.HANDOVER_STATUS_ACCEPTED
    )
    h.vessel.realm = REALM
    h.vessel.entity_id = ENTITY_ID
    h.offered_to.roc_site = site
    h.accepted_at.FromSeconds(int(time.time()))
    return enclose(h.SerializeToString())


def handover_key(handover_id):
    return f"{CHECKLIST_REALM}/@v0/{CHECKLIST_ENTITY}/pubsub/checklist_handover/{handover_id}"


@pytest.mark.e2e
def test_she_is_held_short_and_cleared_by_the_handover(
    bus, connector_process_factory, zenoh_endpoints, tmp_path
):
    seen = _Gate()
    bus.declare_subscriber(
        construct_pubsub_key(REALM, ENTITY_ID, "mandate_gate", "mandate-gate"), seen
    )
    start_gate(connector_process_factory, zenoh_endpoints, tmp_path)
    wait_until_present(bus)

    seen.wait_for(lambda m: m.state == MandateGate.STATE_NO_FIX, 10)

    far = seen.wait_for(
        lambda m: m.state == MandateGate.STATE_CLEAR and m.zone_ahead == "east-sector",
        15,
        feed=sensors(bus, EDGE_M - 4000),
    )
    assert far.watch_site == "ROC-1" and far.hold_id == 0

    held = seen.wait_for(
        lambda m: m.state == MandateGate.STATE_HOLDING,
        15,
        feed=sensors(bus, EDGE_M - 1500),
    )
    assert held.hold_id == 1 and held.hold.radius_m == 400
    assert "hand the watch over" in held.reason

    bus.put(handover_key("h-1"), handover_to("ROC-2"))
    clear = seen.wait_for(
        lambda m: m.state == MandateGate.STATE_CLEAR and m.watch_site == "ROC-2",
        15,
        feed=sensors(bus, EDGE_M - 1500, sog=0.0),
    )
    assert clear.handover_id == "h-1" and clear.hold_id == 1
    assert not clear.HasField("hold")
