"""End-to-end: the composite policy as its own connector.

Runs `entity_health` and `composite_aggregator` as separate processes, which
is the point of the split: the determination is derived from the published
`EntityHealth` message rather than from the evidence producer's internals.
"""

import json
import logging
import threading
import time
from pathlib import Path

import pytest
import zenoh

import keelson

from keelson import construct_pubsub_key, enclose
from keelson.scaffolding import create_zenoh_config

REALM = "test-realm"
ENTITY_ID = "test-vessel"
HEALTH_SOURCE_ID = "health"
AGGREGATOR_SOURCE_ID = "composite"

_logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e


class _AuthorityCollector:
    def __init__(self) -> None:
        self.messages = []

    def __call__(self, sample: zenoh.Sample) -> None:
        try:
            from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority

            _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
            msg = OperationalAuthority()
            msg.ParseFromString(payload)
            self.messages.append(msg)
        except Exception:
            _logger.exception("failed to decode OperationalAuthority sample")

    def wait_for(self, predicate, timeout: float = 10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.messages and predicate(self.messages[-1]):
                return self.messages[-1]
            time.sleep(0.1)
        return self.messages[-1] if self.messages else None


@pytest.mark.e2e
def test_an_essential_failure_vetoes_authority_on_the_wire(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """A healthy fleet plus one dead essential subject.

    The point of the whole design: `composite_score` stays high because the
    fleet really is mostly healthy, while `authority_score` goes to zero
    because a prerequisite is not satisfied. Under a plain mean these could
    not disagree, and the veto could never fire.
    """
    from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority

    # gnss/location_fix is essential and never publishes; the other sources
    # are watched with require_liveliness False so they evaluate on activity.
    config = {
        "publish_rate_hz": 5.0,
        "sources": [
            {
                "name": "gnss",
                "subjects": [
                    {
                        "name": "location_fix",
                        "require_liveliness": False,
                        "inactive_after_s": 1.0,
                        "window_s": 1.0,
                    }
                ],
            }
        ]
        + [
            {
                "name": f"healthy{i}",
                "subjects": [
                    {
                        "name": "length_over_all_m",
                        "require_liveliness": False,
                        "inactive_after_s": 1.0,
                        "window_s": 1.0,
                        "publication_rate_hz": [{"level": "NOMINAL", "min": 0.0}],
                    }
                ],
            }
            for i in range(5)
        ],
    }
    config_path = temp_dir / "health.json"
    config_path.write_text(json.dumps(config))

    # The prerequisite is policy, not evidence: it lives with the ladder in
    # the aggregator's config, not in the watch config that decides levels.
    policy_path = temp_dir / "policy.json"
    policy_path.write_text(
        json.dumps({"essential": [{"source": "gnss", "subject": "location_fix"}]})
    )

    test_conf = create_zenoh_config(
        mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
    )
    session = zenoh.open(test_conf)
    health = None
    aggregator = None
    sub = None
    stop = threading.Event()

    # The healthy sources have to actually publish, or they are INACTIVE too
    # and the composite has nothing honest left to report.
    pubs = [
        session.declare_publisher(
            construct_pubsub_key(REALM, ENTITY_ID, "length_over_all_m", f"healthy{i}")
        )
        for i in range(5)
    ]

    def publish_loop():
        from keelson.payloads.Primitives_pb2 import TimestampedFloat

        while not stop.is_set():
            msg = TimestampedFloat()
            msg.timestamp.FromNanoseconds(time.time_ns())
            msg.value = 25.0
            for pub in pubs:
                pub.put(enclose(msg.SerializeToString()))
            time.sleep(0.1)  # ~10 Hz

    pub_thread = threading.Thread(target=publish_loop, daemon=True)
    pub_thread.start()

    try:
        collector = _AuthorityCollector()
        sub = session.declare_subscriber(
            construct_pubsub_key(
                REALM, ENTITY_ID, "operational_authority", AGGREGATOR_SOURCE_ID
            ),
            collector,
        )

        health = connector_process_factory(
            "entity_health",
            "entity_health2keelson",
            [
                "--realm",
                REALM,
                "--entity-id",
                ENTITY_ID,
                "--source-id",
                HEALTH_SOURCE_ID,
                "--config",
                str(config_path),
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        health.start()

        aggregator = connector_process_factory(
            "composite_aggregator",
            "composite_aggregator2keelson",
            [
                "--realm",
                REALM,
                "--entity-id",
                ENTITY_ID,
                "--source-id",
                AGGREGATOR_SOURCE_ID,
                "--config",
                str(policy_path),
                "--publish-rate-hz",
                "5.0",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        aggregator.start()

        msg = collector.wait_for(lambda m: len(m.active_constraints) > 0, timeout=15.0)
        assert msg is not None, "no OperationalAuthority received"

        assert msg.level == OperationalAuthority.AUTHORITY_LEVEL_MINIMAL_SAFE_MODE
        assert msg.HasField("authority_score")
        assert msg.authority_score == pytest.approx(0.0)

        # The fleet is genuinely mostly healthy, and the message says so.
        assert (
            msg.composite_score > 0.5
        ), f"composite should stay honest, got {msg.composite_score}"

        # The constraint names the limiting component structurally, so a
        # consumer never has to parse `reason`.
        causes = {(c.component_id, c.subject_id) for c in msg.active_constraints}
        assert ("gnss", "location_fix") in causes

        assert msg.source_assessments, "per-source arithmetic must be published"
    finally:
        stop.set()
        pub_thread.join(timeout=2.0)
        if sub is not None:
            sub.undeclare()
        if aggregator is not None:
            aggregator.stop()
        if health is not None:
            health.stop()
        session.close()
