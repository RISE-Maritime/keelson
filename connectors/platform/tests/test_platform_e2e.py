"""
End-to-end tests for the Platform connector.

Tests the platform-geometry static geometry publishing functionality.
"""

import json
import time
from pathlib import Path

import pytest
from mcap.reader import make_reader


@pytest.mark.e2e
def test_platform_geometry_runs_with_valid_config(
    connector_process_factory, temp_dir: Path
):
    """Test that platform-geometry runs successfully with a valid config."""
    config = {
        "name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
        "frame_transforms": [
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "radar",
                "translation_m": {"x": 5.0, "y": 0.0, "z": 10.0},
                "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            }
        ],
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "geometry",
            "--config",
            str(config_path),
            "--interval",
            "1",
        ],
    )
    platform.start()
    time.sleep(2)

    assert platform.is_running(), "platform-geometry should be running"
    platform.stop()


@pytest.mark.e2e
def test_platform_geometry_data_recorded(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that platform-geometry data can be recorded by mcap-record."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    config = {
        "name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "geometry",
            "--config",
            str(config_path),
            "--interval",
            "1",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()
    time.sleep(3)

    platform.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1, "Should have recorded an MCAP file"

    file_size = mcap_files[0].stat().st_size
    assert file_size > 500, "MCAP file should contain platform data"


@pytest.mark.e2e
def test_platform_geometry_publishes_correct_topic(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that platform-geometry publishes to the correct keelson topic format."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    config = {
        "name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "geometry",
            "--config",
            str(config_path),
            "--interval",
            "1",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()
    time.sleep(3)

    platform.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None

        topics = [ch.topic for ch in summary.channels.values()]
        assert len(topics) > 0, "Should have recorded topics"

        entity_topics = [t for t in topics if "test-vessel" in t]
        assert len(entity_topics) > 0, "Topic should contain entity-id"


@pytest.mark.e2e
def test_platform_geometry_multiple_frames(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """Test that platform-geometry publishes data for multiple frame transforms."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    config = {
        "name": "Test Vessel",
        "length_over_all_m": 25.0,
        "breadth_over_all_m": 8.0,
        "frame_transforms": [
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "radar",
                "translation_m": {"x": 5.0, "y": 0.0, "z": 10.0},
                "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            },
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "camera_bow",
                "translation_m": {"x": 10.0, "y": 0.0, "z": 5.0},
                "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            },
            {
                "parent_frame_id": "vessel",
                "child_frame_id": "camera_stern",
                "translation_m": {"x": -5.0, "y": 0.0, "z": 5.0},
                "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 3.14159},
            },
        ],
    }

    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(config))

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    time.sleep(1)

    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "geometry",
            "--config",
            str(config_path),
            "--interval",
            "1",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()
    time.sleep(3)

    platform.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    file_size = mcap_files[0].stat().st_size
    assert file_size > 500, "MCAP file should contain platform geometry data"


_PLATFORM_DIR = Path(__file__).resolve().parents[1]


def _record_platform(
    connector_process_factory, temp_dir: Path, zenoh_endpoints, config_path, entity
) -> dict:
    """Run platform-geometry for a few intervals under a recorder, and return
    every recorded message's bytes by topic."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            "test-realm/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
            # A nearly full developer disk must not turn this into a test of
            # the recorder's free-space guard.
            "--bypass-safeguards",
        ],
    )
    recorder.start()
    time.sleep(1)

    platform = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            entity,
            "--source-id",
            "platform",
            "--config",
            str(config_path),
            "--interval",
            "1",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    platform.start()
    time.sleep(3)

    platform.stop()
    recorder.stop()

    (mcap_file,) = output_dir.glob("*.mcap")
    messages: dict[str, list[bytes]] = {}
    with open(mcap_file, "rb") as f:
        for _schema, channel, message in make_reader(f).iter_messages():
            messages.setdefault(channel.topic, []).append(message.data)
    return messages


@pytest.mark.e2e
def test_a_sensor_station_publishes_its_surveyed_position(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """#285: the position goes out as a LocationFix on the platform's own
    source_id, at the configured lat/lon, naming the root of the transform tree,
    with a covariance a consumer can use."""
    from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
    from keelson.payloads.Primitives_pb2 import TimestampedFloat

    config_path = _PLATFORM_DIR / "example-config-sensor-station.json"
    position = json.loads(config_path.read_text())["position"]

    messages = _record_platform(
        connector_process_factory,
        temp_dir,
        zenoh_endpoints,
        config_path,
        "storakrabban",
    )

    key = "test-realm/@v0/storakrabban/pubsub/location_fix/platform"
    assert key in messages, f"no {key} among {sorted(messages)}"

    fix = LocationFix.FromString(messages[key][-1])
    assert fix.latitude == pytest.approx(position["latitude_deg"])
    assert fix.longitude == pytest.approx(position["longitude_deg"])
    # The link to the transform tree. Without it the station's sensors render
    # unrotated, which looks like data rather than like an error.
    assert fix.frame_id == "storakrabban/base_link"
    assert fix.position_covariance_type == LocationFix.APPROXIMATED
    assert len(fix.position_covariance) == 9

    prefix = "test-realm/@v0/storakrabban/pubsub/"
    horizontal = TimestampedFloat.FromString(
        messages[prefix + "location_fix_accuracy_horizontal_m/platform"][-1]
    )
    vertical = TimestampedFloat.FromString(
        messages[prefix + "location_fix_accuracy_vertical_m/platform"][-1]
    )
    assert horizontal.value == pytest.approx(position["accuracy_horizontal_m"])
    assert vertical.value == pytest.approx(position["accuracy_vertical_m"])


@pytest.mark.e2e
def test_a_vessel_without_a_position_publishes_no_location_fix(
    connector_process_factory, temp_dir: Path, zenoh_endpoints
):
    """#285: a vessel's location_fix comes from GNSS on the same subject; the
    platform connector must not publish one beside it."""
    messages = _record_platform(
        connector_process_factory,
        temp_dir,
        zenoh_endpoints,
        _PLATFORM_DIR / "example-config.json",
        "test-vessel",
    )

    assert any("/test-vessel/pubsub/" in topic for topic in messages)
    assert not [topic for topic in messages if "/pubsub/location_fix" in topic]
