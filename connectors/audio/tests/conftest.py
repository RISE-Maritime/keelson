"""Audio connector fixtures."""

from pathlib import Path

import pytest

from audio_test_helpers import sine, write_wav


@pytest.fixture
def sine_wav(temp_dir: Path) -> Path:
    """Two seconds of 440 Hz, 48 kHz mono."""
    return write_wav(temp_dir / "sine.wav", sine(2.0), 48000)
