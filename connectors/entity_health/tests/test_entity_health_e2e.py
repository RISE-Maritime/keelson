"""End-to-end tests for the entity_health connector.

These tests run the connector against a real Zenoh peer mesh, with
`platform-geometry` acting as a publishing data source. The test
process opens its own Zenoh session to:

  - subscribe to the connector's `entity_health` output and decode
    the protobuf payload directly,
  - drive the connector through `set_config` RPC calls.
"""

import json
import time
from pathlib import Path

import pytest
import zenoh

import keelson
from keelson import construct_pubsub_key, construct_rpc_key
from keelson.payloads.EntityHealth_pb2 import (
    EntityHealth,
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_NOMINAL,
    HEALTH_UNKNOWN,
)
from keelson.scaffolding import create_zenoh_config


REALM = "test-realm"
ENTITY_ID = "test-vessel"
HEALTH_SOURCE_ID = "health"
PLATFORM_SOURCE_ID = "geometry"

HEALTH_KEY = construct_pubsub_key(REALM, ENTITY_ID, "entity_health", HEALTH_SOURCE_ID)
SET_CONFIG_KEY = construct_rpc_key(REALM, ENTITY_ID, "set_config", HEALTH_SOURCE_ID)
LOA_KEY = "test-realm/@v0/test-vessel/pubsub/length_over_all_m/*"
BOA_KEY = "test-realm/@v0/test-vessel/pubsub/breadth_over_all_m/*"


# A "calm" band wide enough to keep the platform's ~1 Hz publishing in NOMINAL.
_CALM_BANDS = [
    {"level": "NOMINAL", "min": 0.5, "max": 5.0},
    {"level": "DEGRADED", "min": 0.1, "max": 10.0},
]


def _make_health_config(
    loa_bands: list[dict] = _CALM_BANDS,
    boa_bands: list[dict] = _CALM_BANDS,
    inactive_after_s: float = 2.0,
    window_s: float = 2.0,
) -> dict:
    """Two-subsystem config so we can verify worst-wins aggregation."""
    return {
        "publish_rate_hz": 5.0,
        "expectations": [
            {
                "name": "loa",
                "key_expr": LOA_KEY,
                "inactive_after_s": inactive_after_s,
                "window_s": window_s,
                "publication_rate_hz": loa_bands,
                "publication_rate_default_level": "CRITICAL",
                "require_liveliness": True,
            },
            {
                "name": "boa",
                "key_expr": BOA_KEY,
                "inactive_after_s": inactive_after_s,
                "window_s": window_s,
                "publication_rate_hz": boa_bands,
                "publication_rate_default_level": "CRITICAL",
                "require_liveliness": True,
            },
        ],
    }


class _HealthCollector:
    """Subscribes to entity_health and decodes EntityHealth payloads."""

    def __init__(self) -> None:
        self.messages: list[EntityHealth] = []

    def __call__(self, sample: zenoh.Sample) -> None:
        try:
            _r, _e, payload = keelson.uncover(sample.payload.to_bytes())
            msg = EntityHealth()
            msg.ParseFromString(payload)
            self.messages.append(msg)
        except Exception:
            pass

    def clear(self) -> None:
        self.messages.clear()

    def wait_for(self, predicate, timeout: float = 6.0) -> EntityHealth | None:
        """Wait until the most recent message satisfies `predicate`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.messages and predicate(self.messages[-1]):
                return self.messages[-1]
            time.sleep(0.1)
        return self.messages[-1] if self.messages else None


def _subsystem(msg: EntityHealth, name: str):
    for s in msg.sources:
        if s.name == name:
            return s
    return None


def _set_config(session: zenoh.Session, new_config: dict) -> None:
    """Send a set_config RPC and wait briefly for the reply."""
    replies: list = []
    session.get(
        SET_CONFIG_KEY,
        lambda r: replies.append(r),
        payload=json.dumps(new_config).encode(),
    )
    # Give the queryable time to respond
    time.sleep(0.5)


@pytest.mark.e2e
def test_entity_health_full_lifecycle(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Walk a two-subsystem config through NOMINAL → DEGRADED → CRITICAL → UNKNOWN.

    Only the `loa` subsystem is degraded across phases; `boa` stays NOMINAL.
    The overall `EntityHealth.level` should track the *worst* subsystem.
    """
    initial_config = _make_health_config()
    config_path = temp_dir / "health.json"
    config_path.write_text(json.dumps(initial_config))

    platform_config_path = temp_dir / "platform.json"
    platform_config_path.write_text(
        json.dumps(
            {
                "vessel_name": "Test Vessel",
                "length_over_all_m": 25.0,
                "breadth_over_all_m": 8.0,
            }
        )
    )

    # --- Test session listens on the shared endpoint so connectors can connect ---
    test_conf = create_zenoh_config(
        mode="peer",
        connect=None,
        listen=[zenoh_endpoints["listen"]],
    )
    session = zenoh.open(test_conf)

    try:
        collector = _HealthCollector()
        sub = session.declare_subscriber(HEALTH_KEY, collector)

        # --- Start the data source (declares liveliness + publishes both fields ~1 Hz) ---
        platform = connector_process_factory(
            "platform",
            "platform-geometry",
            [
                "--realm",
                REALM,
                "--entity-id",
                ENTITY_ID,
                "--source-id",
                PLATFORM_SOURCE_ID,
                "--config",
                str(platform_config_path),
                "--interval",
                "1",
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )
        platform.start()

        # --- Start the connector under test ---
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

        # === Phase 1: both subsystems NOMINAL → entity NOMINAL ===
        time.sleep(4.0)
        msg = collector.wait_for(
            lambda m: _subsystem(m, "loa") is not None
            and _subsystem(m, "boa") is not None
            and _subsystem(m, "loa").level == HEALTH_NOMINAL
            and _subsystem(m, "boa").level == HEALTH_NOMINAL
        )
        assert msg is not None, "no EntityHealth received"
        assert msg.level == HEALTH_NOMINAL
        loa = _subsystem(msg, "loa")
        boa = _subsystem(msg, "boa")
        assert loa.name == "loa"
        assert boa.name == "boa"
        assert loa.level == HEALTH_NOMINAL
        assert boa.level == HEALTH_NOMINAL
        assert loa.detail == "ok", f"loa detail: {loa.detail!r}"
        assert boa.detail == "ok", f"boa detail: {boa.detail!r}"
        assert msg.rate_hz == pytest.approx(5.0)
        assert {s.name for s in msg.sources} == {"loa", "boa"}

        # === Phase 2: degrade only loa → entity DEGRADED, boa still NOMINAL ===
        _set_config(
            session,
            _make_health_config(
                loa_bands=[
                    {"level": "NOMINAL", "min": 5.0, "max": 15.0},
                    {"level": "DEGRADED", "min": 0.2, "max": 20.0},
                ],
            ),
        )
        time.sleep(3.0)
        msg = collector.wait_for(
            lambda m: _subsystem(m, "loa") is not None
            and _subsystem(m, "loa").level == HEALTH_DEGRADED
            and _subsystem(m, "boa") is not None
            and _subsystem(m, "boa").level == HEALTH_NOMINAL
        )
        loa = _subsystem(msg, "loa")
        boa = _subsystem(msg, "boa")
        assert loa.level == HEALTH_DEGRADED, loa
        assert boa.level == HEALTH_NOMINAL, boa
        assert (
            "DEGRADED" in loa.detail
        ), f"expected DEGRADED-band detail on loa, got {loa.detail!r}"
        assert "rate" in loa.detail, f"loa detail: {loa.detail!r}"
        assert boa.detail == "ok", f"boa detail: {boa.detail!r}"
        assert (
            msg.level == HEALTH_DEGRADED
        ), f"entity should reflect worst subsystem (DEGRADED), got {msg.level}"

        # === Phase 3: critical only loa → entity CRITICAL, boa still NOMINAL ===
        _set_config(
            session,
            _make_health_config(
                loa_bands=[
                    {"level": "NOMINAL", "min": 50.0, "max": 100.0},
                    {"level": "DEGRADED", "min": 30.0, "max": 110.0},
                ],
            ),
        )
        time.sleep(3.0)
        msg = collector.wait_for(
            lambda m: _subsystem(m, "loa") is not None
            and _subsystem(m, "loa").level == HEALTH_CRITICAL
            and _subsystem(m, "boa") is not None
            and _subsystem(m, "boa").level == HEALTH_NOMINAL
        )
        loa = _subsystem(msg, "loa")
        boa = _subsystem(msg, "boa")
        assert loa.level == HEALTH_CRITICAL, loa
        assert boa.level == HEALTH_NOMINAL, boa
        assert (
            "outside all rate bands" in loa.detail
        ), f"expected fall-through detail on loa, got {loa.detail!r}"
        assert boa.detail == "ok", f"boa detail: {boa.detail!r}"
        assert (
            msg.level == HEALTH_CRITICAL
        ), f"entity should reflect worst subsystem (CRITICAL), got {msg.level}"

        # === Phase 4: stop platform → both subsystems lose liveliness → UNKNOWN ===
        collector.clear()
        platform.stop()
        msg = collector.wait_for(
            lambda m: _subsystem(m, "loa") is not None
            and _subsystem(m, "loa").level == HEALTH_UNKNOWN
            and _subsystem(m, "boa") is not None
            and _subsystem(m, "boa").level == HEALTH_UNKNOWN,
            timeout=8.0,
        )
        loa = _subsystem(msg, "loa")
        boa = _subsystem(msg, "boa")
        sub.undeclare()
        health.stop()
        assert (
            loa.level == HEALTH_UNKNOWN
        ), f"expected loa UNKNOWN, got {loa.level}: {loa.detail}"
        assert (
            boa.level == HEALTH_UNKNOWN
        ), f"expected boa UNKNOWN, got {boa.level}: {boa.detail}"
        assert (
            msg.level == HEALTH_UNKNOWN
        ), f"entity should be UNKNOWN when all subsystems are UNKNOWN, got {msg.level}"
        assert "liveliness" in loa.detail
        assert "liveliness" in boa.detail
    finally:
        session.close()
