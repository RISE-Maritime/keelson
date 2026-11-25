"""Helpers shared by the audio connector's tests.

Uniquely named (not conftest) so that importing it can never pick up another
connector's test module: connector test directories are not packages.
"""

import wave
from pathlib import Path

import numpy as np


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
