"""Replay determinism of the shipped pipeline, not just the engine: two
connector runs over the same scripted input, with evaluations stamped
from the envelopes' enclosed_at and the clock in data mode, produce a
byte-identical warrant_record payload sequence."""

import threading
import time
from pathlib import Path

import pytest
import yaml
import zenoh

import keelson
from keelson import construct_pubsub_key, enclose
from keelson.scaffolding import create_zenoh_config

from test_engine_unit import ALL_NOMINAL, GNSS_DARK, make_eh

REALM = "test-realm"
ENTITY_ID = "test-vessel"
HEALTH_SOURCE_ID = "health"

EXAMPLE_GRAPH = Path(__file__).resolve().parents[1] / "example-graph.yaml"
S = int(1e9)

# The scripted input: fixed enclosed_at timestamps on a synthetic epoch.
BASE_NS = 1_000_000 * S
SCRIPT = (
    [(BASE_NS + t * S, ALL_NOMINAL) for t in range(0, 8)]
    + [(BASE_NS + t * S, GNSS_DARK) for t in range(8, 14)]
    + [(BASE_NS + t * S, ALL_NOMINAL) for t in range(14, 22)]
)


class _PayloadCollector:
    def __init__(self):
        self.payloads = []
        self.lock = threading.Lock()

    def __call__(self, sample):
        _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
        with self.lock:
            self.payloads.append(payload)

    def snapshot(self):
        with self.lock:
            return list(self.payloads)


def run_pipeline(connector_process_factory, zenoh_endpoints, graph_path, source_id):
    test_conf = create_zenoh_config(
        mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
    )
    session = zenoh.open(test_conf)
    try:
        records = _PayloadCollector()
        session.declare_subscriber(
            construct_pubsub_key(REALM, ENTITY_ID, "warrant_record", source_id),
            records,
        )
        health_publisher = session.declare_publisher(
            construct_pubsub_key(REALM, ENTITY_ID, "entity_health", HEALTH_SOURCE_ID)
        )
        connector = connector_process_factory(
            "warrant_aggregator",
            "warrant_aggregator2keelson",
            [
                "--realm",
                REALM,
                "--entity-id",
                ENTITY_ID,
                "--source-id",
                source_id,
                "--config",
                str(graph_path),
                "--clock",
                "data",
                "--publish-rate-hz",
                "2.0",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        connector.start()
        time.sleep(3)  # session up, subscriptions declared

        for enclosed_at, levels in SCRIPT:
            health_publisher.put(
                enclose(make_eh(levels).SerializeToString(), enclosed_at=enclosed_at)
            )
            time.sleep(0.05)

        # Everything the engine does in data mode is driven by the script's
        # timestamps, so once the last message is processed the stream is
        # complete and stable.
        deadline = time.time() + 10
        stable_since = None
        count = -1
        while time.time() < deadline:
            current = len(records.snapshot())
            if current != count:
                count, stable_since = current, time.time()
            elif stable_since and time.time() - stable_since > 1.5:
                break
            time.sleep(0.2)
        connector.stop()
        return records.snapshot()
    finally:
        session.close()


@pytest.mark.e2e
def test_two_runs_over_the_same_script_are_byte_identical(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    graph = yaml.safe_load(EXAMPLE_GRAPH.read_text())
    graph["requalification_hold_s"] = 2.0
    graph["snapshot_period_s"] = 4.0
    graph["evidence_max_age_s"] = 3.0
    graph_path = temp_dir / "graph.yaml"
    graph_path.write_text(yaml.safe_dump(graph))

    first = run_pipeline(
        connector_process_factory, zenoh_endpoints, graph_path, "warrant"
    )
    second = run_pipeline(
        connector_process_factory, zenoh_endpoints, graph_path, "warrant"
    )

    assert first, "first run produced no warrant_record messages"
    assert len(first) == len(second), (len(first), len(second))
    assert first == second, "record streams differ between identical runs"
