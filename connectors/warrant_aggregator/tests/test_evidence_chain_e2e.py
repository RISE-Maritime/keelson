"""End-to-end, both connectors on a real Zenoh mesh (keelson #237).

entity_health grades a battery's state of charge with the example's content
rule; warrant_aggregator withdraws `endurance_reserve` when it drops below the
relief floor. The record carries the reading and the limit, and the
determination points at it. Nothing in between is synthetic.
"""

import json
import threading
import time
from pathlib import Path

import keelson
import pytest
import yaml
import zenoh
from keelson import construct_pubsub_key, enclose
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.payloads.WarrantRecord_pb2 import WarrantRecord
from keelson.scaffolding import create_zenoh_config

REALM = "test-realm"
ENTITY_ID = "test-vessel"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "evidence-with-numbers"

AUTHORITY_KEY = construct_pubsub_key(
    REALM, ENTITY_ID, "operational_authority", "warrant"
)
RECORD_KEY = construct_pubsub_key(REALM, ENTITY_ID, "warrant_record", "warrant")
CHARGE_KEY = construct_pubsub_key(
    REALM, ENTITY_ID, "battery_state_of_charge_pct", "bms"
)
SINR_KEY = construct_pubsub_key(REALM, ENTITY_ID, "radio_sinr_db", "satcom")
LEVEL = OperationalAuthority.AuthorityLevel


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

    def wait_for(self, predicate, timeout=40.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for msg in self.messages:
                    if predicate(msg):
                        return msg
            time.sleep(0.2)
        return None


def _float(value):
    msg = TimestampedFloat()
    msg.timestamp.FromNanoseconds(time.time_ns())
    msg.value = value
    return enclose(msg.SerializeToString())


def _withdrawn(claim):
    def predicate(m):
        return (
            m.WhichOneof("event") == "standing_transition"
            and m.standing_transition.claim_id == claim
            and m.standing_transition.to_standing
            == WarrantRecord.Standing.STANDING_WITHDRAWN
            and m.standing_transition.rebuttals_fired
        )

    return predicate


@pytest.mark.e2e
def test_a_low_battery_reaches_the_record_with_its_numbers(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    health = json.loads((EXAMPLE / "entity-health.json").read_text())
    health["publish_rate_hz"] = 2.0
    health_path = temp_dir / "entity-health.json"
    health_path.write_text(json.dumps(health))

    graph = yaml.safe_load((EXAMPLE / "claim-graph.yaml").read_text())
    graph["requalification_hold_s"] = 1.0
    graph["snapshot_period_s"] = 2.0
    graph["evidence_max_age_s"] = 5.0
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
        session.declare_subscriber(AUTHORITY_KEY, authority)
        session.declare_subscriber(RECORD_KEY, records)
        charge_pub = session.declare_publisher(CHARGE_KEY)
        sinr_pub = session.declare_publisher(SINR_KEY)

        common = ["--realm", REALM, "--entity-id", ENTITY_ID]
        connect = ["--connect", zenoh_endpoints["connect"]]
        evaluator = connector_process_factory(
            "entity_health",
            "entity_health2keelson",
            common + ["--source-id", "health", "--config", str(health_path)] + connect,
        )
        aggregator = connector_process_factory(
            "warrant_aggregator",
            "warrant_aggregator2keelson",
            common
            + ["--source-id", "warrant", "--config", str(graph_path)]
            + ["--publish-rate-hz", "2.0"]
            + connect,
        )
        evaluator.start()
        aggregator.start()

        stop = threading.Event()
        charge = {"pct": 60.0}

        def pump():
            while not stop.is_set():
                charge_pub.put(_float(charge["pct"]))
                sinr_pub.put(_float(9.0))
                time.sleep(0.3)

        threading.Thread(target=pump, daemon=True).start()
        try:
            top = authority.wait_for(
                lambda m: m.level == LEVEL.AUTHORITY_LEVEL_REMOTE_CONTROLLED
            )
            assert top is not None, "never licensed both claims"

            charge["pct"] = 18.4

            record = records.wait_for(_withdrawn("endurance_reserve"))
            assert record is not None, "no withdrawal of endurance_reserve recorded"
            [fired] = record.standing_transition.rebuttals_fired
            assert fired.id == "below_relief_reserve"
            assert fired.evidence_level == "DEGRADED"
            assert "value=18.4 in DEGRADED band [10.0, 25.0]" in fired.evidence
            assert "NOMINAL is ≥ 25.0" in fired.evidence

            capped = authority.wait_for(
                lambda m: m.level == LEVEL.AUTHORITY_LEVEL_SUPERVISED_REMOTE
                and any(
                    c.warrant.claim_id == "endurance_reserve"
                    for c in m.active_constraints
                )
            )
            assert capped is not None, "the determination never pointed at the record"
            [constraint] = capped.active_constraints
            assert constraint.warrant.source_id == "warrant"
            assert "18.4" not in capped.reason
        finally:
            stop.set()
            evaluator.stop()
            aggregator.stop()
    finally:
        session.close()
