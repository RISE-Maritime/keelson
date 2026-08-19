"""End-to-end test: the connector against a real Zenoh mesh.

The test session plays entity_health, publishing synthetic EntityHealth
messages, and asserts that the connector publishes its determination as
OperationalAuthority (no scores, policy-identified) and its record as
WarrantRecord (transitions and snapshots), and that a withdrawal shows up
in both when the evidence goes dark.
"""

import threading
import time
from pathlib import Path

import pytest
import yaml
import zenoh

import keelson
from keelson import construct_pubsub_key, enclose
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.payloads.WarrantRecord_pb2 import WarrantRecord

from keelson.scaffolding import create_zenoh_config

from test_engine_unit import ALL_NOMINAL, GNSS_DARK, make_eh

REALM = "test-realm"
ENTITY_ID = "test-vessel"
AGGREGATOR_SOURCE_ID = "warrant"
HEALTH_SOURCE_ID = "health"

EXAMPLE_GRAPH = Path(__file__).resolve().parents[1] / "example-graph.yaml"

AUTHORITY_KEY = construct_pubsub_key(
    REALM, ENTITY_ID, "operational_authority", AGGREGATOR_SOURCE_ID
)
RECORD_KEY = construct_pubsub_key(
    REALM, ENTITY_ID, "warrant_record", AGGREGATOR_SOURCE_ID
)
HEALTH_KEY = construct_pubsub_key(REALM, ENTITY_ID, "entity_health", HEALTH_SOURCE_ID)


class _Collector:
    def __init__(self, message_type):
        self.message_type = message_type
        self.messages = []
        self.lock = threading.Lock()

    def __call__(self, sample):
        _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
        msg = self.message_type()
        msg.ParseFromString(payload)
        with self.lock:
            self.messages.append(msg)

    def wait_for(self, predicate, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for msg in self.messages:
                    if predicate(msg):
                        return msg
            time.sleep(0.2)
        return None


@pytest.mark.e2e
def test_withdrawal_reaches_both_subjects(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    # A fast graph: short holds so the test licenses quickly.
    graph = yaml.safe_load(EXAMPLE_GRAPH.read_text())
    graph["requalification_hold_s"] = 1.0
    graph["snapshot_period_s"] = 2.0
    graph["evidence_max_age_s"] = 3.0
    graph_path = temp_dir / "graph.yaml"
    graph_path.write_text(yaml.safe_dump(graph))

    test_conf = create_zenoh_config(
        mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
    )
    session = zenoh.open(test_conf)
    try:
        authority = _Collector(OperationalAuthority)
        records = _Collector(WarrantRecord)
        session.declare_subscriber(AUTHORITY_KEY, authority)
        session.declare_subscriber(RECORD_KEY, records)
        health_publisher = session.declare_publisher(HEALTH_KEY)

        connector = connector_process_factory(
            "warrant_aggregator",
            "warrant_aggregator2keelson",
            [
                "--realm",
                REALM,
                "--entity-id",
                ENTITY_ID,
                "--source-id",
                AGGREGATOR_SOURCE_ID,
                "--config",
                str(graph_path),
                "--publish-rate-hz",
                "2.0",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        connector.start()

        stop = threading.Event()
        levels = {"value": ALL_NOMINAL}

        def pump():
            while not stop.is_set():
                health_publisher.put(
                    enclose(make_eh(levels["value"]).SerializeToString())
                )
                time.sleep(0.5)

        pump_thread = threading.Thread(target=pump, daemon=True)
        pump_thread.start()

        # Phase 1: everything licenses and the level climbs to the top rung.
        full = authority.wait_for(
            lambda m: m.level
            == OperationalAuthority.AuthorityLevel.AUTHORITY_LEVEL_FULL_AUTONOMOUS
        )
        assert full is not None, "never reached FULL_AUTONOMOUS"
        assert not full.HasField("composite_score")
        assert full.policy_id == "warrant_graph/v1"
        assert full.policy_config_digest

        snapshot = records.wait_for(
            lambda m: m.WhichOneof("event") == "snapshot"
            and any(
                s.claim_id == "navigation"
                and s.standing == WarrantRecord.Standing.STANDING_LICENSED
                for s in m.snapshot.claims
            )
        )
        assert snapshot is not None, "no licensed snapshot observed"
        assert snapshot.snapshot.policy_id == "warrant_graph/v1"
        assert snapshot.snapshot.policy_config_digest
        nav = {s.claim_id: s for s in snapshot.snapshot.claims}["navigation"]
        assert nav.statement == "the vessel can navigate"
        assert nav.warrant

        # Phase 2: the main GNSS evidence goes dark; position weakens on
        # the surviving receiver, navigation withdraws, and the withdrawal
        # must reach both subjects with the fired rebuttal on the record.
        levels["value"] = GNSS_DARK
        dropped = authority.wait_for(
            lambda m: m.level
            == OperationalAuthority.AuthorityLevel.AUTHORITY_LEVEL_SUPERVISED_REMOTE
        )
        assert dropped is not None, "level never dropped"
        constrained = {c.component_id for c in dropped.active_constraints}
        # Withdrawn claims only: the weakened position is not a constraint.
        assert constrained == {"gnss_fix", "navigation"}

        weakened = records.wait_for(
            lambda m: m.WhichOneof("event") == "standing_transition"
            and m.standing_transition.claim_id == "position"
            and m.standing_transition.to_standing
            == WarrantRecord.Standing.STANDING_WEAKENED
        )
        assert weakened is not None, "no weakening transition on the wire"

        transition = records.wait_for(
            lambda m: m.WhichOneof("event") == "standing_transition"
            and m.standing_transition.claim_id == "gnss_fix"
            and m.standing_transition.to_standing
            == WarrantRecord.Standing.STANDING_WITHDRAWN
        )
        assert transition is not None, "no withdrawal transition on the wire"
        fired = transition.standing_transition.rebuttals_fired
        assert fired and fired[0].id == "fix_stream_not_nominal"
        assert fired[0].evidence_level == "INACTIVE"

        stop.set()
        pump_thread.join(timeout=2)
    finally:
        session.close()
