"""Camera-specific test fixtures."""

from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture
def test_video(temp_dir: Path) -> Path:
    """Generate a synthetic AVI video file for testing.

    Creates a 320x240 MJPG video at 10 FPS with 30 frames (3 seconds).
    Each frame has a different color so the encoder produces real data.
    """
    video_path = temp_dir / "test_video.avi"
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(video_path), fourcc, 10.0, (320, 240))

    for i in range(30):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        # Vary color per frame so encoder produces distinct frames
        frame[:, :] = (i * 8 % 256, (i * 5 + 50) % 256, (i * 3 + 100) % 256)
        writer.write(frame)

    writer.release()
    assert video_path.exists() and video_path.stat().st_size > 0
    return video_path
