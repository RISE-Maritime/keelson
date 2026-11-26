"""
End-to-end tests for the screen connector.

Pattern: start mcap-record -> start screen connector -> sleep -> stop both ->
validate the MCAP. Skipped where no display can be grabbed (headless CI).
"""

import time

import pytest
from mcap.reader import make_reader


def display_available() -> bool:
    """True when mss can open a display and grab from it."""
    try:
        import mss

        with mss.MSS() as sct:
            sct.grab({"left": 0, "top": 0, "width": 8, "height": 8})
        return True
    except Exception:
        return False


REALM = "test-realm"
ENTITY = "test-vessel"
SOURCE = "screen/0"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not display_available(), reason="no display to capture"),
]


def test_screen_compressed_data_recorded(
    connector_process_factory, temp_dir, zenoh_endpoints
):
    """A monitor capture lands in MCAP as foxglove.CompressedImage frames."""
    from keelson.payloads.foxglove.CompressedImage_pb2 import CompressedImage

    output_dir = temp_dir / "mcap_output"
    output_dir.mkdir()

    recorder = connector_process_factory(
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
    time.sleep(1)

    screen = connector_process_factory(
        "screen",
        "screen",
        [
            "--realm",
            REALM,
            "--entity-id",
            ENTITY,
            "--source-id",
            SOURCE,
            "--monitor",
            "1",
            "--region",
            "0,0,320,240",
            "--fps",
            "5",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
    )
    screen.start()
    time.sleep(4)
    assert screen.is_running(), "Screen connector should still be running"

    screen.stop()
    recorder.stop()

    mcap_files = list(output_dir.glob("*.mcap"))
    assert len(mcap_files) == 1

    expected_topic = f"{REALM}/@v0/{ENTITY}/pubsub/image_compressed/{SOURCE}"
    with open(mcap_files[0], "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        topics = {ch.topic: ch for ch in summary.channels.values()}
        assert expected_topic in topics, f"got {sorted(topics)}"
        schema = summary.schemas[topics[expected_topic].schema_id]
        assert schema.name == "foxglove.CompressedImage"

        frames = [
            CompressedImage.FromString(msg.data)
            for _, channel, msg in reader.iter_messages()
            if channel.topic == expected_topic
        ]
    assert len(frames) >= 5
    assert all(frame.format == "jpeg" for frame in frames)
    assert all(frame.timestamp.ToNanoseconds() > 0 for frame in frames)
