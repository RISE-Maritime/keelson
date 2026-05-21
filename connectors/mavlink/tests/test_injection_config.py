"""Unit tests for the injection-config YAML loader."""

from pathlib import Path
from textwrap import dedent

import pytest

from conftest import injection_config as ic


def _write_yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "injection.yaml"
    p.write_text(dedent(text).lstrip())
    return p


def _load(
    tmp_path: Path,
    text: str,
    *,
    entity_id: str = "motorboat-01",
    source_id: str = "mav/0",
):
    path = _write_yaml(tmp_path, text)
    return ic.load_injection_config(
        path,
        connector_entity_id=entity_id,
        connector_source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidMinimal:
    def test_minimal_gps_input_parses(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix: "external-gnss/0"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
        """,
        )
        assert len(mappings) == 1
        m = mappings[0]
        assert m.spec.mavlink_message == "GPS_INPUT"
        assert m.spec.trigger_subject == "location_fix"
        assert len(m.sources) == 3
        assert m.throttle_s is None
        assert m.max_companion_age_s is None
        assert m.missing_required_companions == []

    def test_short_form_uses_connector_entity_id(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix: "external-gnss/0"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
        """,
            entity_id="motorboat-01",
        )
        src = next(s for s in mappings[0].sources if s.subject == "location_fix")
        assert src.entity_id == "motorboat-01"
        assert src.source_id == "external-gnss/0"

    def test_long_form_with_explicit_entity_id(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix:
                  entity_id: rtk-rover
                  source_id: "**"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
        """,
            entity_id="motorboat-01",
        )
        src = next(s for s in mappings[0].sources if s.subject == "location_fix")
        assert src.entity_id == "rtk-rover"
        assert src.source_id == "**"

    def test_long_form_inherits_entity_when_omitted(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix:
                  source_id: "external-gnss/0"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
        """,
            entity_id="motorboat-01",
        )
        src = next(s for s in mappings[0].sources if s.subject == "location_fix")
        assert src.entity_id == "motorboat-01"

    def test_throttle_and_max_age_parsed(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix: "external-gnss/0"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
              throttle_s: 0.5
              max_companion_age_s: 1.5
        """,
        )
        m = mappings[0]
        assert m.throttle_s == 0.5
        assert m.max_companion_age_s == 1.5

    def test_missing_required_companion_is_warning_not_fatal(self, tmp_path, caplog):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix: "external-gnss/0"
        """,
        )
        m = mappings[0]
        assert "location_fix_quality" in m.missing_required_companions
        assert "location_fix_satellites_visible" in m.missing_required_companions


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="not found"):
            ic.load_injection_config(
                tmp_path / "nope.yaml",
                connector_entity_id="ent",
                connector_source_id="src",
            )

    def test_unparseable_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("GPS_INPUT:\n  sources: [unclosed")
        with pytest.raises(ic.InjectionConfigError, match="parse YAML"):
            ic.load_injection_config(
                p,
                connector_entity_id="ent",
                connector_source_id="src",
            )

    def test_top_level_not_mapping(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="must be a mapping"):
            _load(tmp_path, "- foo\n- bar\n")

    def test_unknown_mavlink_message(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="unknown MAVLink"):
            _load(
                tmp_path,
                """
                GPS_INTPUT:
                  sources:
                    location_fix: "external-gnss/0"
            """,
            )

    def test_subject_not_in_message_registry(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="not relevant"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix: "external-gnss/0"
                    battery_voltage_v: "external-gnss/0"
            """,
            )

    def test_unknown_keelson_subject(self, tmp_path):
        # Manually inject a fake subject that's in the GPS_INPUT spec but not
        # in subjects.yaml. We patch the registry temporarily.
        original = ic.MESSAGE_REGISTRY["GPS_INPUT"]
        ic.MESSAGE_REGISTRY["GPS_INPUT"] = ic.MessageSpec(
            mavlink_message="GPS_INPUT",
            trigger_subject="location_fix",
            optional_companions=("totally_made_up_subject",),
        )
        try:
            with pytest.raises(ic.InjectionConfigError, match="not in subjects.yaml"):
                _load(
                    tmp_path,
                    """
                    GPS_INPUT:
                      sources:
                        location_fix: "external-gnss/0"
                        totally_made_up_subject: "external-gnss/0"
                """,
                )
        finally:
            ic.MESSAGE_REGISTRY["GPS_INPUT"] = original

    def test_missing_trigger_subject(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="must include the trigger"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix_quality: "external-gnss/0"
            """,
            )

    def test_empty_sources(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="non-empty mapping"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources: {}
            """,
            )

    def test_missing_sources(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="`sources` is required"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  throttle_s: 0.2
            """,
            )

    def test_negative_throttle(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="throttle_s.*must be > 0"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix: "external-gnss/0"
                  throttle_s: -0.1
            """,
            )

    def test_non_numeric_max_age(self, tmp_path):
        with pytest.raises(
            ic.InjectionConfigError, match="max_companion_age_s.*must be a number"
        ):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix: "external-gnss/0"
                  max_companion_age_s: "soon"
            """,
            )

    def test_long_form_missing_source_id(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="requires `source_id`"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix:
                      entity_id: rtk-rover
            """,
            )


# ---------------------------------------------------------------------------
# Loopback guard
# ---------------------------------------------------------------------------


class TestLoopbackGuard:
    def test_wildcard_source_id_against_own_entity_rejected(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="connector's own"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix: "**"
            """,
                entity_id="motorboat-01",
                source_id="mav/0",
            )

    def test_exact_match_rejected(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="connector's own"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix: "mav/0"
            """,
                entity_id="motorboat-01",
                source_id="mav/0",
            )

    def test_glob_prefix_match_rejected(self, tmp_path):
        with pytest.raises(ic.InjectionConfigError, match="connector's own"):
            _load(
                tmp_path,
                """
                GPS_INPUT:
                  sources:
                    location_fix: "mav/**"
            """,
                entity_id="motorboat-01",
                source_id="mav/0",
            )

    def test_different_entity_id_allowed_with_wildcard(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix:
                  entity_id: rtk-rover
                  source_id: "**"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
        """,
            entity_id="motorboat-01",
            source_id="mav/0",
        )
        assert len(mappings) == 1

    def test_disjoint_source_id_allowed(self, tmp_path):
        mappings = _load(
            tmp_path,
            """
            GPS_INPUT:
              sources:
                location_fix: "external-gnss/0"
                location_fix_quality: "external-gnss/0"
                location_fix_satellites_visible: "external-gnss/0"
        """,
            entity_id="motorboat-01",
            source_id="mav/0",
        )
        assert len(mappings) == 1
