"""End-to-end tests for the entity_health connector.

These tests run the connector against a real Zenoh peer mesh, with
`platform-geometry` acting as a publishing data source. The test
process opens its own Zenoh session to:

  - subscribe to the connector's `entity_health` output and decode
    the protobuf payload directly,
  - drive the connector through `set_config` RPC calls.
"""

import json
import logging
import threading
import time
from pathlib import Path

import pytest
import zenoh

import keelson
from keelson import construct_pubsub_key, construct_rpc_key, enclose
from keelson.payloads.EntityHealth_pb2 import (
    EntityHealth,
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_NOMINAL,
    HEALTH_UNKNOWN,
)
from keelson.payloads.LocationFixQuality_pb2 import LocationFixQuality
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.scaffolding import create_zenoh_config


REALM = "test-realm"
ENTITY_ID = "test-vessel"
HEALTH_SOURCE_ID = "health"
PLATFORM_SOURCE_ID = "geometry"

HEALTH_KEY = construct_pubsub_key(REALM, ENTITY_ID, "entity_health", HEALTH_SOURCE_ID)
SET_CONFIG_KEY = construct_rpc_key(REALM, ENTITY_ID, "set_config", HEALTH_SOURCE_ID)


# A "calm" band wide enough to keep the platform's ~1 Hz publishing in NOMINAL.
_CALM_BANDS = [
    {"level": "NOMINAL", "min": 0.5, "max": 5.0},
    {"level": "DEGRADED", "min": 0.1, "max": 10.0},
]


def _make_health_config(
    loa_bands: list[dict] | None = None,
    boa_bands: list[dict] | None = None,
    inactive_after_s: float = 2.0,
    window_s: float = 2.0,
) -> dict:
    """One source, two subjects — exercises per-source aggregation."""
    if loa_bands is None:
        loa_bands = _CALM_BANDS
    if boa_bands is None:
        boa_bands = _CALM_BANDS
    return {
        "publish_rate_hz": 5.0,
        "sources": [
            {
                "name": PLATFORM_SOURCE_ID,
                "subjects": [
                    {
                        "name": "length_over_all_m",
                        "inactive_after_s": inactive_after_s,
                        "window_s": window_s,
                        "publication_rate_hz": loa_bands,
                        "publication_rate_default_level": "CRITICAL",
                        "require_liveliness": True,
                    },
                    {
                        "name": "breadth_over_all_m",
                        "inactive_after_s": inactive_after_s,
                        "window_s": window_s,
                        "publication_rate_hz": boa_bands,
                        "publication_rate_default_level": "CRITICAL",
                        "require_liveliness": True,
                    },
                ],
            },
        ],
    }


_logger = logging.getLogger(__name__)


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
            # Don't let a malformed sample tank the test silently — surface it
            # so a "no message received" timeout becomes a parse traceback.
            _logger.exception("failed to decode EntityHealth sample")

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


def _source(msg: EntityHealth, source_name: str):
    for s in msg.sources:
        if s.name == source_name:
            return s
    return None


def _subject(
    msg: EntityHealth, subject_name: str, source_name: str = PLATFORM_SOURCE_ID
):
    src = _source(msg, source_name)
    if src is None:
        return None
    for s in src.subjects:
        if s.name == subject_name:
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
                "name": "Test Vessel",
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
    sub = None
    platform = None
    health = None

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
        # Generous timeout: cold-start (process spawn + Zenoh discovery) +
        # window_s=2 + a couple of ~1 Hz publish ticks to fill the window.
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "breadth_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_NOMINAL
            and _subject(m, "breadth_over_all_m").level == HEALTH_NOMINAL,
            timeout=12.0,
        )
        assert msg is not None, "no EntityHealth received"
        assert msg.level == HEALTH_NOMINAL
        loa = _subject(msg, "length_over_all_m")
        boa = _subject(msg, "breadth_over_all_m")
        assert loa.name == "length_over_all_m"
        assert boa.name == "breadth_over_all_m"
        assert loa.level == HEALTH_NOMINAL
        assert boa.level == HEALTH_NOMINAL
        assert msg.rate_hz == pytest.approx(5.0)
        # Both subjects must hang off the platform source — there is exactly one
        # SourceHealth, named after the publisher's source-id.
        assert [s.name for s in msg.sources] == [PLATFORM_SOURCE_ID]
        assert {s.name for s in msg.sources[0].subjects} == {
            "length_over_all_m",
            "breadth_over_all_m",
        }
        # Source-level rollup is the worst of its subjects (NOMINAL here).
        assert msg.sources[0].level == HEALTH_NOMINAL
        # checks[] should carry the per-check NOMINAL results
        assert {c.name for c in loa.checks} == {"activity", "publication_rate"}
        assert all(c.level == HEALTH_NOMINAL for c in loa.checks)

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
        # window_s=2 + a publish tick at publish_rate_hz=5 → effects visible
        # within ~3s; default 6s timeout in wait_for absorbs scheduler jitter.
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_DEGRADED
            and _subject(m, "breadth_over_all_m") is not None
            and _subject(m, "breadth_over_all_m").level == HEALTH_NOMINAL
        )
        loa = _subject(msg, "length_over_all_m")
        boa = _subject(msg, "breadth_over_all_m")
        assert loa.level == HEALTH_DEGRADED, loa
        assert boa.level == HEALTH_NOMINAL, boa
        loa_rate = next(c for c in loa.checks if c.name == "publication_rate")
        assert (
            loa_rate.level == HEALTH_DEGRADED
        ), f"expected DEGRADED publication_rate on loa, got {loa_rate}"
        assert (
            "DEGRADED" in loa_rate.detail
        ), f"expected DEGRADED-band detail on loa rate check, got {loa_rate.detail!r}"
        assert "rate" in loa_rate.detail, f"loa rate check detail: {loa_rate.detail!r}"
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
        # Same budget as phase 2 — only the band thresholds shifted.
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_CRITICAL
            and _subject(m, "breadth_over_all_m") is not None
            and _subject(m, "breadth_over_all_m").level == HEALTH_NOMINAL
        )
        loa = _subject(msg, "length_over_all_m")
        boa = _subject(msg, "breadth_over_all_m")
        assert loa.level == HEALTH_CRITICAL, loa
        assert boa.level == HEALTH_NOMINAL, boa
        loa_rate = next(c for c in loa.checks if c.name == "publication_rate")
        assert (
            "outside all rate bands" in loa_rate.detail
        ), f"expected fall-through detail on loa rate check, got {loa_rate.detail!r}"
        assert (
            msg.level == HEALTH_CRITICAL
        ), f"entity should reflect worst subsystem (CRITICAL), got {msg.level}"

        # === Phase 4: stop platform → both subsystems lose liveliness → UNKNOWN ===
        collector.clear()
        platform.stop()
        # Liveliness drop propagates almost immediately, but inactive_after_s=2
        # plus Zenoh's liveliness eviction adds slack — 8s is comfortable.
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_UNKNOWN
            and _subject(m, "breadth_over_all_m") is not None
            and _subject(m, "breadth_over_all_m").level == HEALTH_UNKNOWN,
            timeout=8.0,
        )
        loa = _subject(msg, "length_over_all_m")
        boa = _subject(msg, "breadth_over_all_m")
        assert loa.level == HEALTH_UNKNOWN, f"expected loa UNKNOWN, got {loa.level}"
        assert boa.level == HEALTH_UNKNOWN, f"expected boa UNKNOWN, got {boa.level}"
        assert (
            msg.level == HEALTH_UNKNOWN
        ), f"entity should be UNKNOWN when all subsystems are UNKNOWN, got {msg.level}"
        # Liveliness gate emits no checks — UNKNOWN at the source level says it all
        assert list(loa.checks) == []
        assert list(boa.checks) == []
    finally:
        if sub is not None:
            sub.undeclare()
        if health is not None:
            health.stop()
        if platform is not None:
            platform.stop()
        session.close()


@pytest.mark.e2e
def test_measured_publication_rate_tracks_publisher(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """SubjectHealth.measured_publication_rate_hz must reflect the publisher's
    actual rate, and must change when the publisher's rate changes.

    Phase A: publisher at 1s interval (~1 Hz) → measured rate ≈ 1.0
    Phase B: replace with publisher at 2s interval (~0.5 Hz) → measured rate drops
    """
    # Wide rate band so both 1.0 Hz and 0.5 Hz stay NOMINAL — the test is
    # about the *measured* rate, not the level. window_s small so the
    # measurement reacts to the rate change quickly.
    wide_band = [{"level": "NOMINAL", "min": 0.1, "max": 5.0}]
    config = _make_health_config(
        loa_bands=wide_band,
        boa_bands=wide_band,
        inactive_after_s=5.0,
        window_s=3.0,
    )
    config_path = temp_dir / "health.json"
    config_path.write_text(json.dumps(config))

    platform_config_path = temp_dir / "platform.json"
    platform_config_path.write_text(
        json.dumps(
            {
                "name": "Test Vessel",
                "length_over_all_m": 25.0,
                "breadth_over_all_m": 8.0,
            }
        )
    )

    test_conf = create_zenoh_config(
        mode="peer",
        connect=None,
        listen=[zenoh_endpoints["listen"]],
    )
    session = zenoh.open(test_conf)
    sub = None
    platform_fast = None
    platform_slow = None
    health = None

    def _platform(interval_s: int):
        return connector_process_factory(
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
                str(interval_s),
                "--connect",
                zenoh_endpoints["connect"],
            ],
        )

    try:
        collector = _HealthCollector()
        sub = session.declare_subscriber(HEALTH_KEY, collector)

        # --- Phase A: 1 Hz publisher ---
        platform_fast = _platform(1)
        platform_fast.start()

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

        # Wait for a NOMINAL message where the measured rate has stabilised
        # near 1 Hz (window_s=3 → ~3 samples → 1.0 Hz).
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_NOMINAL
            and _subject(m, "length_over_all_m").measured_publication_rate_hz >= 0.8,
            timeout=10.0,
        )
        assert msg is not None, "no EntityHealth received in phase A"
        loa_fast = _subject(msg, "length_over_all_m")
        boa_fast = _subject(msg, "breadth_over_all_m")
        assert (
            loa_fast.measured_publication_rate_hz > 0.0
        ), "measured_publication_rate_hz should be non-zero when samples are flowing"
        assert loa_fast.measured_publication_rate_hz == pytest.approx(
            1.0, abs=0.5
        ), f"expected loa rate ~1.0 Hz, got {loa_fast.measured_publication_rate_hz}"
        assert boa_fast.measured_publication_rate_hz == pytest.approx(
            1.0, abs=0.5
        ), f"expected boa rate ~1.0 Hz, got {boa_fast.measured_publication_rate_hz}"
        fast_rate = loa_fast.measured_publication_rate_hz

        # --- Phase B: swap to 0.5 Hz publisher ---
        platform_fast.stop()
        platform_slow = _platform(2)
        platform_slow.start()

        # The window must drain the 1 Hz samples (3s window) and refill with
        # 0.5 Hz samples, so we allow generous time. We accept the new rate
        # as soon as it has clearly dropped and is positive.
        # Drain old 1 Hz samples from the 3s window + refill with 0.5 Hz —
        # worst case ~3s drain + 2 fresh ticks ≈ 7s; 15s gives ample slack.
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_NOMINAL
            and 0.0 < _subject(m, "length_over_all_m").measured_publication_rate_hz
            and _subject(m, "length_over_all_m").measured_publication_rate_hz
            < fast_rate * 0.8,
            timeout=15.0,
        )
        assert (
            msg is not None
        ), "expected a NOMINAL message with a reduced measured rate after slowing the publisher"
        loa_slow = _subject(msg, "length_over_all_m")
        assert (
            loa_slow.measured_publication_rate_hz < fast_rate
        ), f"slow rate {loa_slow.measured_publication_rate_hz} should be < fast rate {fast_rate}"
        assert loa_slow.measured_publication_rate_hz == pytest.approx(
            0.5, abs=0.4
        ), f"expected loa rate ~0.5 Hz after slowing publisher, got {loa_slow.measured_publication_rate_hz}"
    finally:
        if sub is not None:
            sub.undeclare()
        if health is not None:
            health.stop()
        for p in (platform_fast, platform_slow):
            if p is not None:
                p.stop()
        session.close()


@pytest.mark.e2e
def test_source_health_checks_field_published(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Published SubjectHealth carries structured per-check results.

    Verifies:
      - On NOMINAL, source-level detail is empty and checks[] has the
        publication_rate entry at NOMINAL.
      - After flipping the loa rate band so the observed rate is too slow,
        the matching publication_rate CheckResult goes DEGRADED with a
        non-empty per-check detail string.
    """
    initial_config = _make_health_config()
    config_path = temp_dir / "health.json"
    config_path.write_text(json.dumps(initial_config))

    platform_config_path = temp_dir / "platform.json"
    platform_config_path.write_text(
        json.dumps(
            {
                "name": "Test Vessel",
                "length_over_all_m": 25.0,
                "breadth_over_all_m": 8.0,
            }
        )
    )

    test_conf = create_zenoh_config(
        mode="peer",
        connect=None,
        listen=[zenoh_endpoints["listen"]],
    )
    session = zenoh.open(test_conf)
    sub = None
    platform = None
    health = None

    try:
        collector = _HealthCollector()
        sub = session.declare_subscriber(HEALTH_KEY, collector)

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

        # --- NOMINAL: structured checks[] with publication_rate at NOMINAL ---
        # Cold-start budget: spawn + Zenoh discovery + window_s=2 fill.
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_NOMINAL,
            timeout=8.0,
        )
        assert msg is not None, "no NOMINAL EntityHealth received"
        loa = _subject(msg, "length_over_all_m")
        rate_check = next((c for c in loa.checks if c.name == "publication_rate"), None)
        assert (
            rate_check is not None
        ), f"no publication_rate check in {list(loa.checks)}"
        assert rate_check.level == HEALTH_NOMINAL
        assert rate_check.detail == ""
        # activity check is also part of the structured output now
        activity_check = next((c for c in loa.checks if c.name == "activity"), None)
        assert activity_check is not None
        assert activity_check.level == HEALTH_NOMINAL

        # --- Flip the loa rate band so the observed rate goes DEGRADED ---
        _set_config(
            session,
            _make_health_config(
                loa_bands=[
                    {"level": "NOMINAL", "min": 5.0, "max": 15.0},
                    {"level": "DEGRADED", "min": 0.2, "max": 20.0},
                ],
            ),
        )
        msg = collector.wait_for(
            lambda m: _subject(m, "length_over_all_m") is not None
            and _subject(m, "length_over_all_m").level == HEALTH_DEGRADED,
            timeout=8.0,
        )
        assert (
            msg is not None
        ), "no DEGRADED EntityHealth received after flipping rate band"
        loa = _subject(msg, "length_over_all_m")
        rate_check = next((c for c in loa.checks if c.name == "publication_rate"), None)
        assert rate_check is not None
        assert rate_check.level == HEALTH_DEGRADED
        assert "DEGRADED" in rate_check.detail
        assert "rate" in rate_check.detail
    finally:
        if sub is not None:
            sub.undeclare()
        if health is not None:
            health.stop()
        if platform is not None:
            platform.stop()
        session.close()


# --- gnss + gnss_quality content-rule e2e -------------------------------

GNSS_PUB_SOURCE = "gnss-mock"
GNSS_FIX_KEY = construct_pubsub_key(REALM, ENTITY_ID, "location_fix", GNSS_PUB_SOURCE)
GNSS_QUAL_KEY = construct_pubsub_key(
    REALM, ENTITY_ID, "location_fix_quality", GNSS_PUB_SOURCE
)


def _gnss_health_config() -> dict:
    """One source publishing two subjects (location_fix + location_fix_quality).

    `require_liveliness=False` so the test publisher (a thread, not a real
    connector) doesn't need to declare liveliness tokens. Rate bands are
    relaxed (1-50 Hz NOMINAL) so the publisher's ~10 Hz output keeps
    publication_rate at NOMINAL throughout — the test focuses on content
    rules and structured checks[].
    """
    rate_band = [{"level": "NOMINAL", "min": 1.0, "max": 50.0}]
    return {
        "publish_rate_hz": 5.0,
        "sources": [
            {
                "name": GNSS_PUB_SOURCE,
                "subjects": [
                    {
                        "name": "location_fix",
                        "inactive_after_s": 5.0,
                        "window_s": 2.0,
                        "publication_rate_hz": rate_band,
                        "publication_rate_default_level": "CRITICAL",
                        "require_liveliness": False,
                        "content_rules": [
                            {
                                "field": "latitude",
                                "bands": [{"level": "NOMINAL", "min": -90, "max": 90}],
                                "default_level": "CRITICAL",
                            },
                            {
                                "field": "longitude",
                                "bands": [
                                    {"level": "NOMINAL", "min": -180, "max": 180}
                                ],
                                "default_level": "CRITICAL",
                            },
                        ],
                    },
                    {
                        "name": "location_fix_quality",
                        "inactive_after_s": 5.0,
                        "window_s": 2.0,
                        "publication_rate_hz": rate_band,
                        "publication_rate_default_level": "CRITICAL",
                        "require_liveliness": False,
                        "content_rules": [
                            {
                                "field": "fix_type",
                                "bands": [
                                    {
                                        "level": "NOMINAL",
                                        "equals": ["FIX_3D_RTK", "FIX_3D"],
                                    },
                                    {
                                        "level": "DEGRADED",
                                        "equals": [
                                            "FIX_3D_DGPS",
                                            "FIX_2D",
                                            "GPS_DR",
                                        ],
                                    },
                                    {
                                        "level": "CRITICAL",
                                        "equals": [
                                            "INVALID",
                                            "FIX_NO",
                                            "UNKNOWN",
                                            "TIME_ONLY",
                                            "DR_ONLY",
                                        ],
                                    },
                                ],
                                "default_level": "CRITICAL",
                            },
                        ],
                    },
                ],
            },
        ],
    }


@pytest.mark.e2e
def test_gnss_content_rules_with_structured_checks(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Walk gnss + gnss_quality through NOMINAL → DEGRADED (fix downgrades
    RTK→DGPS) → CRITICAL (latitude out of range), verifying that the
    structured `checks[]` field carries one entry per check (rate +
    each content rule) and that levels/details flip independently.
    """
    config_path = temp_dir / "health.json"
    config_path.write_text(json.dumps(_gnss_health_config()))

    test_conf = create_zenoh_config(
        mode="peer",
        connect=None,
        listen=[zenoh_endpoints["listen"]],
    )
    session = zenoh.open(test_conf)

    fix_pub = session.declare_publisher(GNSS_FIX_KEY)
    qual_pub = session.declare_publisher(GNSS_QUAL_KEY)

    state_lock = threading.Lock()
    state = {
        "lat": 45.0,
        "lon": 10.0,
        "fix": LocationFixQuality.FIX_3D_RTK,
        "stop": False,
    }

    def publish_loop() -> None:
        while True:
            with state_lock:
                if state["stop"]:
                    return
                lat, lon, fix = state["lat"], state["lon"], state["fix"]
            fix_msg = LocationFix()
            fix_msg.latitude = lat
            fix_msg.longitude = lon
            fix_pub.put(enclose(fix_msg.SerializeToString()))
            qual_msg = LocationFixQuality()
            qual_msg.fix_type = fix
            qual_pub.put(enclose(qual_msg.SerializeToString()))
            time.sleep(0.1)  # ~10 Hz

    pub_thread = threading.Thread(target=publish_loop, daemon=True)
    pub_thread.start()

    sub = None
    health = None

    try:
        collector = _HealthCollector()
        sub = session.declare_subscriber(HEALTH_KEY, collector)

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

        # === Phase 1: All NOMINAL ===
        msg = collector.wait_for(
            lambda m: _subject(m, "location_fix", GNSS_PUB_SOURCE) is not None
            and _subject(m, "location_fix", GNSS_PUB_SOURCE).level == HEALTH_NOMINAL
            and _subject(m, "location_fix_quality", GNSS_PUB_SOURCE) is not None
            and _subject(m, "location_fix_quality", GNSS_PUB_SOURCE).level
            == HEALTH_NOMINAL,
            timeout=10.0,
        )
        assert msg is not None, "no all-NOMINAL EntityHealth received"
        assert msg.level == HEALTH_NOMINAL
        gnss = _subject(msg, "location_fix", GNSS_PUB_SOURCE)
        qual = _subject(msg, "location_fix_quality", GNSS_PUB_SOURCE)
        assert {c.name for c in gnss.checks} == {
            "activity",
            "publication_rate",
            "latitude",
            "longitude",
        }
        assert all(c.level == HEALTH_NOMINAL for c in gnss.checks)
        assert all(c.detail == "" for c in gnss.checks)
        assert {c.name for c in qual.checks} == {
            "activity",
            "publication_rate",
            "fix_type",
        }
        assert all(c.level == HEALTH_NOMINAL for c in qual.checks)
        assert all(c.detail == "" for c in qual.checks)

        # === Phase 2: fix downgrades RTK → DGPS → gnss_quality DEGRADED ===
        with state_lock:
            state["fix"] = LocationFixQuality.FIX_3D_DGPS
        msg = collector.wait_for(
            lambda m: _subject(m, "location_fix_quality", GNSS_PUB_SOURCE) is not None
            and _subject(m, "location_fix_quality", GNSS_PUB_SOURCE).level
            == HEALTH_DEGRADED,
            timeout=10.0,
        )
        gnss = _subject(msg, "location_fix", GNSS_PUB_SOURCE)
        qual = _subject(msg, "location_fix_quality", GNSS_PUB_SOURCE)
        assert (
            gnss.level == HEALTH_NOMINAL
        ), "gnss should be unaffected by fix_type change"
        assert qual.level == HEALTH_DEGRADED
        fix_check = next(c for c in qual.checks if c.name == "fix_type")
        rate_check_qual = next(c for c in qual.checks if c.name == "publication_rate")
        assert fix_check.level == HEALTH_DEGRADED
        assert "FIX_3D_DGPS" in fix_check.detail
        assert "DEGRADED" in fix_check.detail
        assert (
            rate_check_qual.level == HEALTH_NOMINAL
        ), "rate check on gnss_quality should still be NOMINAL"
        assert rate_check_qual.detail == ""
        # Entity tracks the worst across sources
        assert msg.level == HEALTH_DEGRADED

        # === Phase 3: latitude out of range → gnss CRITICAL, quality stays DEGRADED ===
        with state_lock:
            state["lat"] = 200.0  # outside [-90, 90]
        msg = collector.wait_for(
            lambda m: _subject(m, "location_fix", GNSS_PUB_SOURCE) is not None
            and _subject(m, "location_fix", GNSS_PUB_SOURCE).level == HEALTH_CRITICAL
            and _subject(m, "location_fix_quality", GNSS_PUB_SOURCE) is not None
            and _subject(m, "location_fix_quality", GNSS_PUB_SOURCE).level
            == HEALTH_DEGRADED,
            timeout=10.0,
        )
        gnss = _subject(msg, "location_fix", GNSS_PUB_SOURCE)
        qual = _subject(msg, "location_fix_quality", GNSS_PUB_SOURCE)
        assert gnss.level == HEALTH_CRITICAL
        lat_check = next(c for c in gnss.checks if c.name == "latitude")
        lon_check = next(c for c in gnss.checks if c.name == "longitude")
        rate_check_gnss = next(c for c in gnss.checks if c.name == "publication_rate")
        assert lat_check.level == HEALTH_CRITICAL
        assert "latitude=200" in lat_check.detail
        assert "outside" in lat_check.detail
        # longitude and rate stay clean — proves per-check independence
        assert lon_check.level == HEALTH_NOMINAL
        assert lon_check.detail == ""
        assert rate_check_gnss.level == HEALTH_NOMINAL
        assert rate_check_gnss.detail == ""
        # gnss_quality still DEGRADED (fix_type unchanged from phase 2)
        assert qual.level == HEALTH_DEGRADED
        # Entity reflects worst-of (CRITICAL > DEGRADED)
        assert msg.level == HEALTH_CRITICAL
    finally:
        # Stop the publisher thread first so undeclare doesn't race the loop.
        with state_lock:
            state["stop"] = True
        pub_thread.join(timeout=2.0)
        if sub is not None:
            sub.undeclare()
        if health is not None:
            health.stop()
        fix_pub.undeclare()
        qual_pub.undeclare()
        session.close()
