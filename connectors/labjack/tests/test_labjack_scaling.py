"""Unit tests for config resolution: per-channel voltage scaling + checks."""

import math

import pytest
from jsonschema import validate, ValidationError

from conftest import labjack2keelson

resolve_scale_offset = labjack2keelson.resolve_scale_offset


def _apply(v_meas, channel):
    """Resolve a channel's (scale, offset) and apply it, as the read loop does."""
    scale, offset = resolve_scale_offset(channel)
    return v_meas * scale + offset


class TestResolveScaleOffset:
    def test_default_passthrough(self):
        """No divider and no scale/offset -> measured value unchanged."""
        assert resolve_scale_offset({"ain": "AIN0"}) == (1.0, 0.0)
        assert _apply(2.5, {"ain": "AIN0"}) == pytest.approx(2.5)

    def test_divider_equal_resistors_doubles(self):
        """R1 == R2 attenuates by 2, so the true voltage is 2x measured."""
        channel = {"divider": {"r1_ohms": 470000, "r2_ohms": 470000}}
        assert resolve_scale_offset(channel) == (pytest.approx(2.0), 0.0)
        assert _apply(1.6, channel) == pytest.approx(3.2)

    def test_divider_general_ratio(self):
        """true = measured * (R1 + R2) / R2."""
        channel = {"divider": {"r1_ohms": 9000, "r2_ohms": 1000}}
        # 3.0 V measured across a /10 divider -> 30.0 V true.
        assert _apply(3.0, channel) == pytest.approx(30.0)

    def test_scale_only(self):
        """LJTick-Divider-/4 style: multiply by 4."""
        assert _apply(2.5, {"scale": 4.0}) == pytest.approx(10.0)

    def test_scale_and_offset(self):
        assert _apply(2.0, {"scale": 3.0, "offset": 1.5}) == pytest.approx(7.5)

    def test_offset_only(self):
        assert _apply(2.0, {"offset": -0.25}) == pytest.approx(1.75)

    def test_divider_takes_precedence_when_present(self):
        """A divider is used even if stray scale/offset keys exist."""
        channel = {"divider": {"r1_ohms": 1000, "r2_ohms": 1000}, "scale": 99.0}
        assert _apply(1.0, channel) == pytest.approx(2.0)


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


class TestEmbeddedJsonSchema:
    """The schema is embedded in the binary (no separate file); guard it."""

    def test_minimal_valid_config(self):
        validate(
            {"channels": [{"ain": "AIN0", "source_id": "a"}]},
            labjack2keelson.JSON_SCHEMA,
        )

    def test_empty_channels_rejected(self):
        with pytest.raises(ValidationError):
            validate({"channels": []}, labjack2keelson.JSON_SCHEMA)

    def test_bad_ain_pattern_rejected(self):
        with pytest.raises(ValidationError):
            validate(
                {"channels": [{"ain": "PIN0", "source_id": "a"}]},
                labjack2keelson.JSON_SCHEMA,
            )

    def test_divider_and_scale_mutually_exclusive(self):
        with pytest.raises(ValidationError):
            validate(
                {
                    "channels": [
                        {
                            "ain": "AIN0",
                            "source_id": "a",
                            "divider": {"r1_ohms": 1, "r2_ohms": 1},
                            "scale": 2.0,
                        }
                    ]
                },
                labjack2keelson.JSON_SCHEMA,
            )

    def test_unknown_property_rejected(self):
        with pytest.raises(ValidationError):
            validate(
                {"channels": [{"ain": "AIN0", "source_id": "a", "bogus": 1}]},
                labjack2keelson.JSON_SCHEMA,
            )


class TestCheckSubjects:
    def test_default_subject_ok(self):
        labjack2keelson._check_subjects(
            {"channels": [{"ain": "AIN0", "source_id": "a"}]}
        )

    def test_known_float_subject_ok(self):
        labjack2keelson._check_subjects(
            {
                "channels": [
                    {"ain": "AIN0", "source_id": "a", "subject": "battery_voltage_v"}
                ]
            }
        )

    def test_unknown_subject_rejected(self):
        with pytest.raises(ValueError, match="not a known Keelson subject"):
            labjack2keelson._check_subjects(
                {
                    "channels": [
                        {"ain": "AIN0", "source_id": "a", "subject": "not_a_subject"}
                    ]
                }
            )

    def test_wrong_payload_type_rejected(self):
        # location_fix is a known subject, but not a TimestampedFloat.
        with pytest.raises(ValueError, match="expected keelson.TimestampedFloat"):
            labjack2keelson._check_subjects(
                {
                    "channels": [
                        {"ain": "AIN0", "source_id": "a", "subject": "location_fix"}
                    ]
                }
            )
