#!/usr/bin/env python3

"""Parser + validator for the mavlink2keelson injection-mapping YAML file.

The file is operator-authored. Each top-level key is a MAVLink output
message name (e.g. GPS_INPUT). Each value declares:

  sources              required. Map of `keelson_subject` -> source_id pattern
                       (short form: string value, entity_id = connector's own)
                       or `{entity_id?, source_id}` (long form).
  throttle_s           optional float. Minimum interval between emissions.
  max_companion_age_s  optional float. Skip emission if any companion's
                       last-known sample is older than this (relative to
                       the trigger sample).

The connector holds a fixed per-MAVLink-message registry that describes
the trigger subject, required + optional companion subjects, and the
emission function. The file only configures which producer fills each
subject and how often.

Validation is fail-fast at startup: bad files raise InjectionConfigError
with a human-readable message so misconfigured deployments crash loudly
rather than silently dropping injection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import yaml

import keelson  # for subject -> schema lookup

logger = logging.getLogger("mavlink2keelson.injection_config")


class InjectionConfigError(ValueError):
    """Raised when the injection-config YAML is invalid."""


# ---------------------------------------------------------------------------
# Per-MAVLink-message registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageSpec:
    """Describes one MAVLink output message the connector knows how to emit.

    Held in the connector code (not in the file). The file's `sources`
    block is validated against the union of trigger + required + optional
    subjects listed here.
    """

    # MAVLink message name (e.g. "GPS_INPUT") - the file's top-level key.
    mavlink_message: str
    # Keelson subject whose arrival fires an emission.
    trigger_subject: str
    # Companions the connector *needs* to produce a meaningful frame.
    # Missing required companions cause a startup warning + per-message
    # default fallback.
    required_companions: tuple[str, ...] = ()
    # Companions whose absence is fine; the connector sets the matching
    # MAVLink ignore bits / leaves the field at zero.
    optional_companions: tuple[str, ...] = ()


# v1: only GPS_INPUT is supported. Adding a new MAVLink message means
# adding a MessageSpec here + an emit function in mavlink2keelson.py.
MESSAGE_REGISTRY: dict[str, MessageSpec] = {
    "GPS_INPUT": MessageSpec(
        mavlink_message="GPS_INPUT",
        trigger_subject="location_fix",
        required_companions=(
            "location_fix_quality",
            "location_fix_satellites_visible",
        ),
        optional_companions=(
            "location_fix_hdop",
            "location_fix_vdop",
            "location_fix_accuracy_horizontal_m",
            "location_fix_accuracy_vertical_m",
            "speed_over_ground_knots",
            "course_over_ground_deg",
            "climb_rate_mps",
        ),
    ),
}


# ---------------------------------------------------------------------------
# Parsed config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSpec:
    """One subject the connector subscribes to as part of an injection
    mapping. `entity_id` is the Keelson entity to subscribe under;
    `source_id` is the source-id glob pattern within that entity."""

    subject: str
    entity_id: str
    source_id: str


@dataclass
class InjectionMapping:
    """One entry from the injection-config file, post-validation. Holds
    everything the connector needs to wire the mapping into skarv."""

    spec: MessageSpec
    sources: list[SourceSpec] = field(default_factory=list)
    throttle_s: Optional[float] = None
    max_companion_age_s: Optional[float] = None
    missing_required_companions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_injection_config(
    path: str | Path,
    *,
    connector_entity_id: str,
    connector_source_id: str,
) -> list[InjectionMapping]:
    """Parse + validate the YAML file at `path`. Returns one
    InjectionMapping per enabled MAVLink message.

    `connector_entity_id` is the connector's own --entity-id, used as the
    default `entity_id` when a source uses the short form.

    `connector_source_id` is the connector's own --source-id, compared
    against each resolved source_id pattern to catch loopback (the
    connector subscribing to its own publications).

    Raises InjectionConfigError if the file is unparseable, references
    unknown MAVLink messages or Keelson subjects, omits a required
    trigger subject, or would loop the connector's own publications back
    into itself.
    """
    raw = _read_yaml(Path(path))
    if not isinstance(raw, Mapping):
        raise InjectionConfigError(
            f"top level of {path} must be a mapping of MAVLink-message-name -> "
            f"settings; got {type(raw).__name__}"
        )

    mappings: list[InjectionMapping] = []
    for mavlink_name, settings in raw.items():
        if not isinstance(mavlink_name, str):
            raise InjectionConfigError(
                f"top-level key must be a string MAVLink message name; got "
                f"{mavlink_name!r}"
            )
        spec = MESSAGE_REGISTRY.get(mavlink_name)
        if spec is None:
            raise InjectionConfigError(
                f"unknown MAVLink message {mavlink_name!r}; supported: "
                f"{sorted(MESSAGE_REGISTRY)}"
            )
        if not isinstance(settings, Mapping):
            raise InjectionConfigError(
                f"{mavlink_name}: settings must be a mapping; got "
                f"{type(settings).__name__}"
            )
        mappings.append(
            _parse_mapping(
                spec=spec,
                settings=settings,
                connector_entity_id=connector_entity_id,
                connector_source_id=connector_source_id,
            )
        )
    return mappings


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise InjectionConfigError(f"injection-config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise InjectionConfigError(f"failed to parse YAML at {path}: {exc}") from exc


def _parse_mapping(
    *,
    spec: MessageSpec,
    settings: Mapping[str, Any],
    connector_entity_id: str,
    connector_source_id: str,
) -> InjectionMapping:
    sources_raw = settings.get("sources")
    if not isinstance(sources_raw, Mapping) or not sources_raw:
        raise InjectionConfigError(
            f"{spec.mavlink_message}: `sources` is required and must be a non-empty mapping"
        )

    known_subjects: set[str] = {
        spec.trigger_subject,
        *spec.required_companions,
        *spec.optional_companions,
    }
    sources: list[SourceSpec] = []
    seen_subjects: set[str] = set()
    for subj, raw_value in sources_raw.items():
        if not isinstance(subj, str):
            raise InjectionConfigError(
                f"{spec.mavlink_message}: source subject key must be string; "
                f"got {subj!r}"
            )
        if subj in seen_subjects:
            raise InjectionConfigError(
                f"{spec.mavlink_message}: duplicate source subject {subj!r}"
            )
        seen_subjects.add(subj)
        if subj not in known_subjects:
            raise InjectionConfigError(
                f"{spec.mavlink_message}: subject {subj!r} is not relevant for "
                f"this MAVLink message; allowed: {sorted(known_subjects)}"
            )
        if not keelson.is_subject_well_known(subj):
            raise InjectionConfigError(
                f"{spec.mavlink_message}: subject {subj!r} is not in subjects.yaml"
            )
        sources.append(
            _parse_source_entry(
                subject=subj,
                raw=raw_value,
                mavlink_name=spec.mavlink_message,
                default_entity_id=connector_entity_id,
            )
        )

    # Trigger subject must be present in sources.
    if spec.trigger_subject not in seen_subjects:
        raise InjectionConfigError(
            f"{spec.mavlink_message}: `sources` must include the trigger "
            f"subject {spec.trigger_subject!r}"
        )

    # Missing required companions are warned, not fatal: the connector
    # falls back to per-message defaults (e.g. fix_type=3, sats=6).
    missing_required = [s for s in spec.required_companions if s not in seen_subjects]
    for s in missing_required:
        logger.warning(
            "%s: required companion subject %r missing from injection config; "
            "connector will use a default placeholder",
            spec.mavlink_message,
            s,
        )

    throttle_s = _parse_optional_positive_float(
        settings,
        "throttle_s",
        spec.mavlink_message,
    )
    max_age_s = _parse_optional_positive_float(
        settings,
        "max_companion_age_s",
        spec.mavlink_message,
    )

    # Loopback guard.
    for src in sources:
        if src.entity_id == connector_entity_id and _patterns_overlap(
            src.source_id, connector_source_id
        ):
            raise InjectionConfigError(
                f"{spec.mavlink_message}: source_id {src.source_id!r} on "
                f"entity {src.entity_id!r} would match the connector's own "
                f"--source-id {connector_source_id!r} — feeding the "
                f"connector's own publications back into the autopilot. "
                f"Pick a more specific source_id pattern."
            )

    return InjectionMapping(
        spec=spec,
        sources=sources,
        throttle_s=throttle_s,
        max_companion_age_s=max_age_s,
        missing_required_companions=missing_required,
    )


def _parse_source_entry(
    *,
    subject: str,
    raw: Any,
    mavlink_name: str,
    default_entity_id: str,
) -> SourceSpec:
    """Resolve the short-form (string) or long-form (mapping) of a single
    `sources` entry into a SourceSpec."""
    if isinstance(raw, str):
        return SourceSpec(
            subject=subject,
            entity_id=default_entity_id,
            source_id=raw,
        )
    if isinstance(raw, Mapping):
        entity_id = raw.get("entity_id", default_entity_id)
        source_id = raw.get("source_id")
        if source_id is None:
            raise InjectionConfigError(
                f"{mavlink_name}: source {subject!r} long-form requires " f"`source_id`"
            )
        if not isinstance(entity_id, str) or not entity_id:
            raise InjectionConfigError(
                f"{mavlink_name}: source {subject!r} `entity_id` must be a "
                f"non-empty string"
            )
        if not isinstance(source_id, str) or not source_id:
            raise InjectionConfigError(
                f"{mavlink_name}: source {subject!r} `source_id` must be a "
                f"non-empty string"
            )
        return SourceSpec(subject=subject, entity_id=entity_id, source_id=source_id)
    raise InjectionConfigError(
        f"{mavlink_name}: source {subject!r} must be either a source_id string "
        f"or a mapping with `entity_id`/`source_id`; got {type(raw).__name__}"
    )


def _parse_optional_positive_float(
    settings: Mapping[str, Any],
    key: str,
    mavlink_name: str,
) -> Optional[float]:
    if key not in settings:
        return None
    val = settings[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise InjectionConfigError(
            f"{mavlink_name}: `{key}` must be a number; got {type(val).__name__}"
        )
    val = float(val)
    if val <= 0.0:
        raise InjectionConfigError(f"{mavlink_name}: `{key}` must be > 0; got {val}")
    return val


def _patterns_overlap(pattern: str, source_id: str) -> bool:
    """Lightweight check: does the source_id pattern match the connector's
    own source_id? Catches the obvious cases (`**`, exact match, glob with
    trailing wildcard). Not a full key-expression intersection — Zenoh's
    own subscriber installation would catch a perfect false-negative."""
    if pattern == source_id:
        return True
    if pattern in ("**", "*"):
        return True
    # Treat zenoh-style globs informally: `prefix/**` matches anything
    # starting with `prefix/`.
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if source_id == prefix or source_id.startswith(prefix + "/"):
            return True
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        if (
            source_id.startswith(prefix + "/")
            and "/" not in source_id[len(prefix) + 1 :]
        ):
            return True
    return False


def summarise(mappings: Iterable[InjectionMapping]) -> str:
    """Human-readable summary of loaded mappings for logging at startup."""
    lines = []
    for m in mappings:
        srcs = ", ".join(f"{s.subject}<-{s.entity_id}/{s.source_id}" for s in m.sources)
        extras = []
        if m.throttle_s is not None:
            extras.append(f"throttle_s={m.throttle_s}")
        if m.max_companion_age_s is not None:
            extras.append(f"max_companion_age_s={m.max_companion_age_s}")
        if m.missing_required_companions:
            extras.append(f"missing_required={m.missing_required_companions}")
        suffix = f" ({'; '.join(extras)})" if extras else ""
        lines.append(f"  {m.spec.mavlink_message}: {srcs}{suffix}")
    return "\n".join(lines) if lines else "  (no mappings)"
