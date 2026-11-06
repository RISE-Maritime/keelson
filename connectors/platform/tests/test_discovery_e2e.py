"""End-to-end: a client finds a platform with no registry and no storage (keelson #191).

Liveliness says what the platform connector publishes and what it serves;
`configurable/v1.get_config` says what the platform is.
"""

import json
import time
from pathlib import Path

import pytest
import zenoh
from keelson.scaffolding import (
    Interface,
    Publication,
    create_zenoh_config,
    list_interfaces,
    list_platforms,
    list_publications,
)

REALM = "test-realm"


@pytest.mark.e2e
def test_a_platform_is_found_by_asking(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    config = {
        "name": "Mast 1",
        "platform_type": "sensor_station",
        "frame_transforms": [
            {
                "parent_frame_id": "mast1/base_link",
                "child_frame_id": "mast1/lidar",
                "translation_m": {"x": 0.0, "y": 0.0, "z": -12.0},
                "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            }
        ],
    }
    vessel = {"name": "Unnamed"}  # no platform_type: served as the default
    connectors = []
    for entity, doc in (("mast1", config), ("boat", vessel)):
        path = temp_dir / f"{entity}.json"
        path.write_text(json.dumps(doc))
        connectors.append(
            connector_process_factory(
                "platform",
                "platform-geometry",
                ["--realm", REALM, "--entity-id", entity, "--source-id", "geometry"]
                + ["--config", str(path), "--interval", "1"]
                + ["--connect", zenoh_endpoints["connect"]],
            )
        )

    session = zenoh.open(
        create_zenoh_config(
            mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
        )
    )
    try:
        for c in connectors:
            c.start()

        deadline = time.time() + 20
        platforms = []
        while time.time() < deadline and len(platforms) < 2:
            platforms = list_platforms(session, REALM, timeout=2.0)
            time.sleep(0.5)

        found = {(p.entity_id, p.source_id): p.config for p in platforms}
        assert set(found) == {("boat", "geometry"), ("mast1", "geometry")}, found
        assert found[("mast1", "geometry")]["platform_type"] == "sensor_station"
        assert found[("boat", "geometry")]["platform_type"] == "vessel"

        pubs = list_publications(session, REALM, "mast1")
        assert Publication("mast1", "frame_transform", "geometry", False) in pubs
        assert Publication("mast1", "configuration_json", "geometry", False) in pubs

        assert Interface("mast1", "configurable", "v1", "geometry") in list_interfaces(
            session, REALM, "mast1"
        )
    finally:
        for c in connectors:
            c.stop()
        session.close()
