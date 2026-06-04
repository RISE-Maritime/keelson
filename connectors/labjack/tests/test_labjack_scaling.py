"""Unit tests for the per-channel voltage scaling math."""

import math

import pytest

from conftest import labjack2keelson

scale_reading = labjack2keelson.scale_reading


class TestScaleReading:
    def test_default_passthrough(self):
        """No divider and no scale/offset -> measured value unchanged."""
        assert scale_reading(2.5, {"ain": "AIN0", "source_id": "a"}) == pytest.approx(
            2.5
        )

    def test_divider_equal_resistors_doubles(self):
        """R1 == R2 attenuates by 2, so the true voltage is 2x measured."""
        channel = {"divider": {"r1_ohms": 470000, "r2_ohms": 470000}}
        assert scale_reading(1.6, channel) == pytest.approx(3.2)

    def test_divider_general_ratio(self):
        """true = measured * (R1 + R2) / R2."""
        channel = {"divider": {"r1_ohms": 9000, "r2_ohms": 1000}}
        # 3.0 V measured across a /10 divider -> 30.0 V true.
        assert scale_reading(3.0, channel) == pytest.approx(30.0)

    def test_scale_only(self):
        """LJTick-Divider-/4 style: multiply by 4."""
        assert scale_reading(2.5, {"scale": 4.0}) == pytest.approx(10.0)

    def test_scale_and_offset(self):
        assert scale_reading(2.0, {"scale": 3.0, "offset": 1.5}) == pytest.approx(7.5)

    def test_offset_only(self):
        assert scale_reading(2.0, {"offset": -0.25}) == pytest.approx(1.75)

    def test_divider_takes_precedence_when_present(self):
        """A divider is used even if stray scale/offset keys exist."""
        channel = {"divider": {"r1_ohms": 1000, "r2_ohms": 1000}, "scale": 99.0}
        assert scale_reading(1.0, channel) == pytest.approx(2.0)


class TestSimulatedReading:
    def test_in_range(self):
        for ain in ("AIN0", "AIN1", "AIN7"):
            for t in (0.0, 5.0, 12.3, 100.0):
                v = labjack2keelson._simulated_reading(ain, t)
                assert 0.0 <= v <= 3.3 + 1e-9

    def test_distinct_channels_differ(self):
        # Different AINs are phase-shifted, so they generally differ.
        a = labjack2keelson._simulated_reading("AIN0", 3.0)
        b = labjack2keelson._simulated_reading("AIN1", 3.0)
        assert not math.isclose(a, b)


class TestUniqueSourceIds:
    def test_rejects_duplicates(self):
        config = {
            "channels": [
                {"ain": "AIN0", "source_id": "dup"},
                {"ain": "AIN1", "source_id": "dup"},
            ]
        }
        with pytest.raises(ValueError):
            labjack2keelson._check_unique_source_ids(config)

    def test_accepts_unique(self):
        config = {
            "channels": [
                {"ain": "AIN0", "source_id": "a"},
                {"ain": "AIN1", "source_id": "b"},
            ]
        }
        labjack2keelson._check_unique_source_ids(config)  # no raise
