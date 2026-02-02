"""Liveliness utilities for Keelson health monitoring."""

import logging

import zenoh

from keelson import construct_liveliness_key

logger = logging.getLogger(__name__)


def declare_liveliness_token(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
):
    """Declare a liveliness token for a source.

    The returned token must be stored by the caller for as long as the source
    is alive. Dropping or undeclaring the token signals a leave event to
    liveliness subscribers.

    Args:
        session: Active Zenoh session.
        base_path: Base path for Keelson keys.
        entity_id: Entity identifier.
        source_id: Source identifier (e.g. ``gnss/0``).

    Returns:
        A Zenoh liveliness token handle.
    """
    key = construct_liveliness_key(base_path, entity_id, source_id)
    return session.liveliness().declare_token(key)
