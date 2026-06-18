"""Zenoh adapter for the transport-neutral QoS profiles in ``keelson.qos``.

Maps the string-valued profiles to ``zenoh.*`` enums and provides a one-call
helper that declares a publisher with the QoS appropriate for a subject::

    from keelson.scaffolding.qos_zenoh import declare_publisher_for_subject

    pub = declare_publisher_for_subject(session, key, "location_fix")
    pub.put(envelope)

This keeps the ``zenoh`` dependency out of the core SDK while giving connectors
a consistent, subject-driven QoS policy instead of hand-tuned per-connector
constants.
"""

import logging
from typing import Union

import zenoh

from keelson import qos, get_subject_from_pubsub_key
from keelson.qos import QoSProfile

logger = logging.getLogger("keelson")

_PRIORITY = {
    "REAL_TIME": zenoh.Priority.REAL_TIME,
    "INTERACTIVE_HIGH": zenoh.Priority.INTERACTIVE_HIGH,
    "INTERACTIVE_LOW": zenoh.Priority.INTERACTIVE_LOW,
    "DATA_HIGH": zenoh.Priority.DATA_HIGH,
    "DATA": zenoh.Priority.DATA,
    "DATA_LOW": zenoh.Priority.DATA_LOW,
    "BACKGROUND": zenoh.Priority.BACKGROUND,
}

_CONGESTION_CONTROL = {
    "DROP": zenoh.CongestionControl.DROP,
    "BLOCK": zenoh.CongestionControl.BLOCK,
}

_RELIABILITY = {
    "RELIABLE": zenoh.Reliability.RELIABLE,
    "BEST_EFFORT": zenoh.Reliability.BEST_EFFORT,
}


def zenoh_publisher_kwargs(profile: Union[QoSProfile, str]) -> dict:
    """Translate a QoSProfile (or subject name) into declare_publisher kwargs.

    Accepts either a ``QoSProfile`` or a subject name (resolved via
    ``keelson.qos.qos_for``). Returns a dict ready to splat into
    ``session.declare_publisher(key, **kwargs)`` or ``session.put(key, payload,
    **kwargs)``.
    """
    if isinstance(profile, str):
        profile = qos.qos_for(profile)

    return {
        "priority": _PRIORITY[profile.priority],
        "congestion_control": _CONGESTION_CONTROL[profile.congestion_control],
        "reliability": _RELIABILITY[profile.reliability],
        "express": profile.express,
    }


def declare_publisher_for_subject(
    session: zenoh.Session,
    key_expr: str,
    subject: str,
    **overrides,
) -> zenoh.Publisher:
    """Declare a publisher with the QoS profile assigned to ``subject``.

    Any keyword in ``overrides`` (e.g. ``congestion_control=...``) takes
    precedence over the profile, for the rare case a connector must deviate.
    """
    kwargs = zenoh_publisher_kwargs(subject)
    kwargs.update(overrides)
    return session.declare_publisher(key_expr, **kwargs)


def declare_publisher(
    session: zenoh.Session,
    key_expr: str,
    **overrides,
) -> zenoh.Publisher:
    """Declare a publisher, deriving its QoS from the subject in ``key_expr``.

    Drop-in for ``session.declare_publisher(key)``: the subject is parsed out of
    the keelson pub/sub key (``.../pubsub/{subject}/{source_id}``) and its QoS
    profile applied. Keys that don't parse fall back to the default profile.
    ``overrides`` take precedence over the profile.
    """
    try:
        subject = get_subject_from_pubsub_key(key_expr)
    except ValueError:
        logger.warning(
            "Could not parse a subject from key %r; using default QoS.", key_expr
        )
        subject = ""  # qos.qos_for() resolves the unknown subject to the default
    return declare_publisher_for_subject(session, key_expr, subject, **overrides)


def put(
    session: zenoh.Session,
    key_expr: str,
    payload,
    **overrides,
) -> None:
    """``session.put`` with QoS derived from the subject in ``key_expr``.

    The ``session.put()`` counterpart of :func:`declare_publisher`, for
    connectors that publish one-shot without caching a publisher. Keys that
    don't parse fall back to the default profile; ``overrides`` win.

    Of the four QoS fields, ``priority``, ``congestion_control`` and ``express``
    are per-sample and applied here; ``reliability`` is per-publisher in Zenoh
    and cannot be set on a one-shot put. Rather than silently downgrade a
    best-effort subject to the session default (RELIABLE), this raises — a
    best-effort subject must go through a declared publisher
    (:func:`declare_publisher`), which can carry reliability. In practice only
    the high-rate ``transient`` frame subjects are best-effort, and those always
    use declared publishers anyway.
    """
    try:
        subject = get_subject_from_pubsub_key(key_expr)
    except ValueError:
        logger.warning(
            "Could not parse a subject from key %r; using default QoS.", key_expr
        )
        subject = ""

    profile = qos.qos_for(subject)
    if profile.reliability == "BEST_EFFORT":
        raise ValueError(
            f"Subject {subject!r} uses the {profile.name!r} profile (BEST_EFFORT), "
            f"which session.put() cannot express — reliability is per-publisher in "
            f"Zenoh. Declare a publisher instead: "
            f"keelson.scaffolding.declare_publisher(session, key)."
        )

    kwargs = zenoh_publisher_kwargs(profile)
    kwargs.update(overrides)
    kwargs.pop("reliability", None)  # session.put() does not accept reliability
    session.put(key_expr, payload, **kwargs)
