"""Audio connector fixtures."""

import importlib.util
import wave
from importlib.machinery import SourceFileLoader
from pathlib import Path

import numpy as np
import pytest

BIN_ROOT = Path(__file__).resolve().parent.parent / "bin"


def load_bin(filename: str):
    """Import a bin/ script (not a package) as a module."""
    name = filename.removesuffix(".py").replace("-", "_")
    loader = SourceFileLoader(name, str(BIN_ROOT / filename))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sine(seconds: float, rate: int = 48000, channels: int = 1, freq: float = 440.0):
    """An int16 (samples, channels) sine at about -12 dBFS."""
    t = np.arange(int(seconds * rate)) / rate
    mono = (np.sin(2 * np.pi * freq * t) * 8000).astype(np.int16)
    return np.repeat(mono[:, None], channels, axis=1)


def write_wav(path: Path, pcm: np.ndarray, rate: int) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(pcm.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.astype("<i2").tobytes())
    return path


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        data = wav.readframes(wav.getnframes())
    return np.frombuffer(data, dtype="<i2").reshape(-1, channels), rate


@pytest.fixture
def sine_wav(temp_dir: Path) -> Path:
    """Two seconds of 440 Hz, 48 kHz mono."""
    return write_wav(temp_dir / "sine.wav", sine(2.0), 48000)
