"""
End-to-end tests for the LabJack connector.

Uses --simulate mode so no LabJack hardware (or native LJM library) is needed.
"""

import json
import time

import pytest
import zenoh

import keelson
from keelson.payloads.Primitives_pb2 import TimestampedFloat


REALM = "test-realm"
ENTITY = "test-rov"


def _write_config(temp_dir):
    config = {
        "poll_interval_s": 0.2,
        "channels": [
            {
                "ain": "AIN0",
                "source_id": "voltage_ch0",
                "subject": "analog_voltage_v",
                "divider": {"r1_ohms": 470000, "r2_ohms": 470000},
            }
        ],
    }
    path = temp_dir / "labjack.json"
    path.write_text(json.dumps(config))
    return path


@pytest.mark.e2e
def test_labjack_simulate_runs(connector_process_factory, temp_dir, zenoh_endpoints):
    """Connector starts and stays alive in --simulate mode."""
    config_path = _write_config(temp_dir)
    labjack = connector_process_factory(
        "labjack",
        "labjack",
        [
            "--realm",
            REALM,
            "--entity-id",
            ENTITY,
            "--config",
            str(config_path),
            "--simulate",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    labjack.start()
    time.sleep(2)
    assert labjack.is_running(), "labjack connector should still be running"
    labjack.stop()


@pytest.mark.e2e
def test_labjack_publishes_scaled_voltage(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """A scaled TimestampedFloat is published on the analog_voltage_v key."""
    config_path = _write_config(temp_dir)

    received = []

    conf = zenoh.Config()
    conf.insert_json5("mode", json.dumps("peer"))
    conf.insert_json5("listen/endpoints", json.dumps([zenoh_endpoints["listen"]]))

    key = keelson.construct_pubsub_key(REALM, ENTITY, "analog_voltage_v", "voltage_ch0")

    with zenoh.open(conf) as session:

        def _on_sample(sample):
            _, _, payload_bytes = keelson.uncover(sample.payload.to_bytes())
            tf = TimestampedFloat()
            tf.ParseFromString(payload_bytes)
            received.append(tf.value)

        sub = session.declare_subscriber(key, _on_sample)

        labjack = connector_process_factory(
            "labjack",
            "labjack",
            [
                "--realm",
                REALM,
                "--entity-id",
                ENTITY,
                "--config",
                str(config_path),
                "--simulate",
                "--mode",
                "peer",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        labjack.start()
        time.sleep(3)
        labjack.stop()

        sub.undeclare()

    assert received, f"Expected published voltages on {key}"
    # Simulated AIN is 0..3.3 V; a /2 divider doubles it -> 0..6.6 V true.
    for value in received:
        assert 0.0 <= value <= 6.7, f"Scaled voltage {value} out of expected range"
