"""CLI tests for the audio connector's binaries."""

import pytest

BASE = ["--realm", "r", "--entity-id", "e", "--source-id", "audio/x"]


@pytest.mark.parametrize(
    "binary, prog, flags",
    [
        (
            "audio2keelson.py",
            "audio2keelson",
            ["--device", "--url", "--format", "--frame-ms"],
        ),
        (
            "keelson2audio.py",
            "keelson2audio",
            ["--key", "--output-file", "--jitter-frames"],
        ),
        ("audio-export.py", "audio-export", ["--output-dir", "--split-gap", "--topic"]),
    ],
)
def test_help(run_connector, binary, prog, flags):
    result = run_connector("audio", binary, ["--help"])
    assert result.returncode == 0, result.stderr
    assert f"usage: {prog}" in result.stdout
    for flag in flags:
        assert flag in result.stdout


def test_capture_requires_identity(run_connector):
    result = run_connector("audio", "audio2keelson.py", ["--url", "x.wav"])
    assert result.returncode == 2
    assert "--realm" in result.stderr and "--source-id" in result.stderr


def test_capture_requires_a_source(run_connector):
    result = run_connector("audio", "audio2keelson.py", BASE)
    assert result.returncode == 2
    assert "one of the arguments" in result.stderr


def test_capture_rejects_an_impossible_opus_setup(run_connector):
    result = run_connector(
        "audio", "audio2keelson.py", BASE + ["--url", "x.wav", "--sample-rate", "44100"]
    )
    assert result.returncode == 2
    assert "Opus sample rate" in result.stderr


def test_sink_requires_a_key(run_connector):
    result = run_connector("audio", "keelson2audio.py", ["--output-file", "x.wav"])
    assert result.returncode == 2
    assert "--key" in result.stderr


def test_export_reports_a_missing_file(run_connector, temp_dir):
    result = run_connector("audio", "audio-export.py", [str(temp_dir / "nope.mcap")])
    assert result.returncode == 1
