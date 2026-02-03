"""
End-to-end tests for the camera connector.

Pattern: start mcap-record → start camera connector → sleep → stop both → validate.
"""

import time

import pytest
from mcap.reader import make_reader


REALM = "test-realm"
ENTITY = "test-vessel"
SOURCE = "cam0"


def _camera_base_args(realm, entity, source, video_path):
    """Return the base CLI arguments common to all camera tests."""
    return [
        "--realm",
        realm,
        "--entity-id",
        entity,
        "--source-id",
        source,
        "--camera-url",
        str(video_path),
    ]


def _start_recorder(factory, output_dir, zenoh_endpoints):
    """Start an mcap-record process and return it."""
    recorder = factory(
        "mcap",
        "mcap-record",
        [
            "--key",
            f"{REALM}/@v0/**",
            "--output-folder",
            str(output_dir),
            "--mode",
            "peer",
            "--listen",
            zenoh_endpoints["listen"],
        ],
    )
    recorder.start()
    return recorder


def _get_mcap_topics(mcap_file):
    """Read an MCAP file and return the set of topic strings."""
    with open(mcap_file, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        if summary is None:
            return set()
        return {ch.topic for ch in summary.channels.values()}


@pytest.mark.e2e
def test_camera_runs_with_video_file(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """Connector starts and stays alive while reading a synthetic video file."""
    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "jpeg",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(2)
    assert camera.is_running(), "Camera connector should still be running"
    camera.stop()


@pytest.mark.e2e
def test_camera_compressed_data_recorded(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """--send jpeg produces MCAP data on the image_compressed topic."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = _start_recorder(connector_process_factory, output_dir, zenoh_endpoints)
    time.sleep(1)

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "jpeg",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)

    camera.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    assert mcap_files[0].stat().st_size > 500

    topics = _get_mcap_topics(mcap_files[0])
    compressed_topics = [t for t in topics if "image_compressed" in t]
    assert len(compressed_topics) > 0, f"Expected image_compressed topic, got {topics}"


@pytest.mark.e2e
def test_camera_raw_data_recorded(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """--send raw produces MCAP data on the image_raw topic."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = _start_recorder(connector_process_factory, output_dir, zenoh_endpoints)
    time.sleep(1)

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "raw",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)

    camera.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    assert mcap_files[0].stat().st_size > 500

    topics = _get_mcap_topics(mcap_files[0])
    raw_topics = [t for t in topics if "image_raw" in t]
    assert len(raw_topics) > 0, f"Expected image_raw topic, got {topics}"


@pytest.mark.e2e
def test_camera_webp_format(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """--send webp produces MCAP data."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = _start_recorder(connector_process_factory, output_dir, zenoh_endpoints)
    time.sleep(1)

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "webp",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)

    camera.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    assert mcap_files[0].stat().st_size > 500


@pytest.mark.e2e
def test_camera_png_format(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """--send png produces MCAP data."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = _start_recorder(connector_process_factory, output_dir, zenoh_endpoints)
    time.sleep(1)

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "png",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)

    camera.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    assert mcap_files[0].stat().st_size > 500


@pytest.mark.e2e
def test_camera_with_frame_id(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """Connector runs correctly with --frame-id argument."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = _start_recorder(connector_process_factory, output_dir, zenoh_endpoints)
    time.sleep(1)

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "jpeg",
            "--frame-id",
            "front_camera",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)

    camera.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1
    assert mcap_files[0].stat().st_size > 500


@pytest.mark.e2e
def test_camera_save_frames_to_disk(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """--save jpeg --save-path <dir> writes image files to disk."""
    save_dir = temp_dir / "saved_frames"
    save_dir.mkdir()

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--save",
            "jpeg",
            "--save-path",
            str(save_dir),
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)
    camera.stop()

    saved_files = list(save_dir.glob("*.jpeg"))
    assert len(saved_files) > 0, f"Expected saved JPEG files in {save_dir}"


@pytest.mark.e2e
def test_camera_only_publishes_to_correct_topic(
    connector_process_factory, temp_dir, test_video, zenoh_endpoints
):
    """--send jpeg publishes to image_compressed only, not image_raw."""
    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = _start_recorder(connector_process_factory, output_dir, zenoh_endpoints)
    time.sleep(1)

    camera = connector_process_factory(
        "camera",
        "camera",
        _camera_base_args(REALM, ENTITY, SOURCE, test_video)
        + [
            "--send",
            "jpeg",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    camera.start()
    time.sleep(4)

    camera.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    topics = _get_mcap_topics(mcap_files[0])
    compressed_topics = [t for t in topics if "image_compressed" in t]
    raw_topics = [t for t in topics if "image_raw" in t]

    assert len(compressed_topics) > 0, f"Expected image_compressed topic, got {topics}"
    assert len(raw_topics) == 0, f"Should NOT have image_raw topic, got {topics}"
