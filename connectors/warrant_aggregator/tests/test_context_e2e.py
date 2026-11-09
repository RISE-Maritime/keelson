"""End-to-end: the connector resolves the leg being sailed and couples it with health (#414).

The test session plays three parts: entity_health (synthetic grades), the
vessel (route_execution naming an edition and a leg), and the router's storage
(a queryable answering that edition's key). The connector must GET the edition,
read the leg's labels and withdraw the claim only when traffic and visibility
coincide.
"""

import threading
import time
from pathlib import Path

import keelson
import pytest
import yaml
import zenoh
from keelson import construct_pubsub_key, enclose
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.payloads.Route_pb2 import Route
from keelson.payloads.RouteExecution_pb2 import RouteExecution
from keelson.payloads.WarrantRecord_pb2 import WarrantRecord
from keelson.scaffolding import create_zenoh_config
from test_engine_unit import make_eh

REALM = "test-realm"
ENTITY = "test-vessel"
EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "context-envelope"
    / "claim-graph.yaml"
)
VIS = ("weather", "visibility_range_m")
GUST = ("weather", "true_wind_speed_max_mps")


class _Collector:
    def __init__(self, message_type):
        self.message_type, self.messages, self.lock = message_type, [], threading.Lock()

    def __call__(self, sample):
        _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
        with self.lock:
            self.messages.append(self.message_type.FromString(payload))

    def wait_for(self, predicate, timeout=40.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for msg in self.messages:
                    if predicate(msg):
                        return msg
            time.sleep(0.2)
        return None


def _route():
    route = Route(route_id="r1", route_edition_number=3)
    wp = route.waypoints.waypoint.add(id="wp-a", operational_context=1)  # OPEN_SEA
    wp.leg.colreg_traffic_intensity = 4  # HEAVY
    route.waypoints.waypoint.add(id="wp-b")
    return route


@pytest.mark.e2e
def test_heavy_traffic_on_the_leg_couples_with_poor_visibility(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    graph = yaml.safe_load(EXAMPLE.read_text())
    graph.update(
        requalification_hold_s=1.0, snapshot_period_s=2.0, evidence_max_age_s=5.0
    )
    graph_path = temp_dir / "graph.yaml"
    graph_path.write_text(yaml.safe_dump(graph))

    session = zenoh.open(
        create_zenoh_config(
            mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
        )
    )
    try:
        authority = _Collector(OperationalAuthority)
        records = _Collector(WarrantRecord)
        session.declare_subscriber(
            construct_pubsub_key(REALM, ENTITY, "operational_authority", "warrant"),
            authority,
        )
        session.declare_subscriber(
            construct_pubsub_key(REALM, ENTITY, "warrant_record", "warrant"), records
        )
        edition_key = construct_pubsub_key(REALM, ENTITY, "route", "r1/edition/3")
        stored = enclose(_route().SerializeToString())
        fetched = threading.Event()

        def storage(query):
            fetched.set()
            query.reply(edition_key, stored)

        session.declare_queryable(edition_key, storage)
        health = session.declare_publisher(
            construct_pubsub_key(REALM, ENTITY, "entity_health", "health")
        )
        execution = session.declare_publisher(
            construct_pubsub_key(REALM, ENTITY, "route_execution", "nav")
        )

        connector = connector_process_factory(
            "warrant_aggregator",
            "warrant_aggregator2keelson",
            ["--realm", REALM, "--entity-id", ENTITY, "--source-id", "warrant"]
            + ["--config", str(graph_path), "--publish-rate-hz", "2.0"]
            + ["--connect", zenoh_endpoints["connect"]],
        )
        connector.start()

        stop = threading.Event()
        levels = {"value": {VIS: "NOMINAL", GUST: "NOMINAL"}}

        def pump():
            msg = RouteExecution(current_leg_from_waypoint_id="wp-a")
            msg.route_ref.route_id, msg.route_ref.route_edition_number = "r1", 3
            while not stop.is_set():
                execution.put(enclose(msg.SerializeToString()))
                health.put(enclose(make_eh(levels["value"]).SerializeToString()))
                time.sleep(0.3)

        threading.Thread(target=pump, daemon=True).start()
        try:
            remote = authority.wait_for(
                lambda m: m.level
                == OperationalAuthority.AuthorityLevel.AUTHORITY_LEVEL_REMOTE_CONTROLLED
            )
            assert (
                remote is not None
            ), "never licensed on nominal weather in heavy traffic"
            assert fetched.is_set(), "the edition was never read from storage"

            levels["value"] = {VIS: "DEGRADED", GUST: "NOMINAL"}
            record = records.wait_for(
                lambda m: m.WhichOneof("event") == "standing_transition"
                and m.standing_transition.claim_id == "remote_navigation"
                and m.standing_transition.to_standing
                == WarrantRecord.Standing.STANDING_WITHDRAWN
                and m.standing_transition.rebuttals_fired
            )
            assert record is not None, "the coupled limit never withdrew the claim"
            [fired] = record.standing_transition.rebuttals_fired
            assert fired.id == "heavy_traffic_in_poor_visibility"
            assert "context traffic_intensity=HEAVY" in fired.evidence
            assert "route r1 edition 3, leg from wp-a" in fired.evidence
            assert fired.evidence_level == "DEGRADED"
        finally:
            stop.set()
            connector.stop()
    finally:
        session.close()
