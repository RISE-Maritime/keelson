"""Well-known QoS profiles for subjects.

Transport-neutral layer over ``qos.yaml``. This module deliberately does NOT
import ``zenoh`` so the core SDK stays vendor-agnostic; the mapping from these
string values to ``zenoh.*`` enums lives in
``keelson.scaffolding.qos_zenoh``.

Typical use (in a connector, via the scaffolding adapter)::

    from keelson.scaffolding.qos_zenoh import declare_publisher_for_subject
    pub = declare_publisher_for_subject(session, key, "location_fix")

Or to inspect a profile directly::

    from keelson import qos
    qos.qos_for("radar_spoke")        # QoSProfile(name='transient', ...)
"""

import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Dict

import yaml

logger = logging.getLogger("keelson")

_PACKAGE_ROOT = Path(__file__).parent

# Allowed values — kept here (not in qos.yaml) so a typo in the YAML is caught
# at load time rather than surfacing as a cryptic enum error in a connector.
_PRIORITIES = {
    "REAL_TIME",
    "INTERACTIVE_HIGH",
    "INTERACTIVE_LOW",
    "DATA_HIGH",
    "DATA",
    "DATA_LOW",
    "BACKGROUND",
}
_CONGESTION_CONTROLS = {"DROP", "BLOCK"}
_RELIABILITIES = {"RELIABLE", "BEST_EFFORT"}


@dataclass(frozen=True)
class QoSProfile:
    """A transport-neutral QoS profile for a subject.

    Attributes are plain strings/bools (see ``qos.yaml`` for semantics) so this
    type carries no dependency on any particular transport.
    """

    name: str
    priority: str
    congestion_control: str
    reliability: str
    express: bool


_PROFILES: Dict[str, QoSProfile] = {}
_SUBJECT_PROFILES: Dict[str, str] = {}
_DEFAULT_PROFILE = "default"


def _validate(profile: QoSProfile) -> None:
    if profile.priority not in _PRIORITIES:
        raise ValueError(
            f"QoS profile {profile.name!r}: unknown priority {profile.priority!r}"
        )
    if profile.congestion_control not in _CONGESTION_CONTROLS:
        raise ValueError(
            f"QoS profile {profile.name!r}: unknown congestion_control "
            f"{profile.congestion_control!r}"
        )
    if profile.reliability not in _RELIABILITIES:
        raise ValueError(
            f"QoS profile {profile.name!r}: unknown reliability {profile.reliability!r}"
        )


def add_qos_profiles(path_to_qos_yaml: Path) -> None:
    """Load (or merge) QoS profiles and subject assignments from a qos.yaml.

    Mirrors ``add_well_known_subjects_and_proto_definitions`` in ``__init__``.
    Missing file is tolerated (every subject then resolves to the default
    profile) so the SDK is importable before ``generate_python.sh`` has copied
    the bundled qos.yaml into the package.
    """
    global _DEFAULT_PROFILE

    if not path_to_qos_yaml.exists():
        logger.warning(
            "qos.yaml not found at %s; all subjects will use the built-in "
            "default profile.",
            path_to_qos_yaml,
        )
        return

    with path_to_qos_yaml.open() as fh:
        doc = yaml.safe_load(fh) or {}

    for name, fields in (doc.get("profiles") or {}).items():
        profile = QoSProfile(
            name=name,
            priority=str(fields["priority"]),
            congestion_control=str(fields["congestion_control"]),
            reliability=str(fields["reliability"]),
            express=bool(fields["express"]),
        )
        _validate(profile)
        _PROFILES[name] = profile

    if (default := doc.get("default")) is not None:
        _DEFAULT_PROFILE = str(default)

    for subject, profile_name in (doc.get("subjects") or {}).items():
        if profile_name not in _PROFILES:
            raise ValueError(
                f"Subject {subject!r} references unknown QoS profile "
                f"{profile_name!r}"
            )
        _SUBJECT_PROFILES[subject] = profile_name


# A hard-coded fallback so qos_for() always returns something sensible even if
# qos.yaml is absent. Matches the `default` profile in the bundled qos.yaml.
_FALLBACK = QoSProfile(
    name="default",
    priority="DATA",
    congestion_control="DROP",
    reliability="RELIABLE",
    express=False,
)


def get_profile(name: str) -> QoSProfile:
    """Return a named profile, or raise KeyError if it is not defined."""
    return _PROFILES[name]


def profile_name_for(subject: str) -> str:
    """Return the profile *name* assigned to a subject (or the default)."""
    return _SUBJECT_PROFILES.get(subject, _DEFAULT_PROFILE)


def qos_for(subject: str) -> QoSProfile:
    """Return the QoS profile for a well-known subject.

    Unlisted subjects resolve to the configured default profile. If qos.yaml
    was never loaded, returns a built-in ``default`` fallback.
    """
    name = profile_name_for(subject)
    profile = _PROFILES.get(name)
    if profile is None:
        return (
            _FALLBACK
            if name == _FALLBACK.name
            else _PROFILES.get(_DEFAULT_PROFILE, _FALLBACK)
        )
    return profile


# Load the bundled profiles on import (best-effort).
add_qos_profiles(_PACKAGE_ROOT / "qos.yaml")
