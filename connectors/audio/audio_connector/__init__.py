"""Shared building blocks for the audio connector's binaries.

- ``codec``: PCM blocks <-> ``keelson.AudioFrame`` (Opus via PyAV, or PCM pass-through)
- ``jitter``: sequence-ordered playout buffer for a live stream
- ``sources``: capture from a sound device or any URL PyAV can open
- ``export``: MCAP recordings -> ``.ogg`` / ``.wav`` files

None of these import zenoh, so they are unit-testable without a bus.
"""
