"""Discovery over the bus: what is published, what can be asked, which platforms exist.

Keelson defines no "what do you publish" or "what can I ask you" procedure
(protocol-specification.md section 5.5, keelson #191). Liveliness already
answers both, and a procedure would restate it and drift from it:

- a source's **subject-level tokens** are its exact data keys,
  ``{base}/@v0/{entity}/pubsub/{subject}/{source_id}`` (section 5.2);
- its **interface-level tokens** are what it can be asked,
  ``{base}/@v0/{entity}/@rpc/{interface}/{version}/*/{source_id}`` (section 3.5).

Metadata beyond that comes from the source itself, over ``configurable/v1``.
:func:`list_platforms` is that recipe for platforms: every platform connector
serves its configuration there, and a platform document always carries
``platform_type``.

Every function takes an open ``zenoh.Session`` and returns plain tuples, so a
caller can render, filter or diff them without importing zenoh types.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, NamedTuple, Optional

logger = logging.getLogger("keelson.discovery")

DEFAULT_TIMEOUT_S = 2.0


class Publication(NamedTuple):
    """One advertised (entity, subject, source): a key a sample can arrive on."""

    entity_id: str
    subject: str
    source_id: str
    # True for a target-scoped token (section 5.2): the source publishes this
    # subject about OTHER entities, under `.../{source_id}/@target/{target}`.
    about_targets: bool


class Interface(NamedTuple):
    """One served (entity, interface, version, source)."""

    entity_id: str
    interface: str
    version: str
    source_id: str


class PlatformInfo(NamedTuple):
    """A platform, found by asking it: where it is, and its configuration."""

    entity_id: str
    source_id: str
    config: Dict[str, Any]


def _after_v0(key: str) -> Optional[List[str]]:
    """The chunks after `@v0`, or None for a key that is not keelson's."""
    chunks = str(key).split("/")
    try:
        return chunks[chunks.index("@v0") + 1 :]
    except ValueError:
        return None


def classify_publication(key: str) -> Optional[Publication]:
    """A subject-level liveliness token as a :class:`Publication`; None for
    any other token (source-level, legacy coarse, RPC) or a malformed key.

    Classified by literal chunks, never by count, because a source_id may
    span several (section 5.5)."""
    rest = _after_v0(key)
    if not rest or len(rest) < 4 or rest[1] != "pubsub":
        return None  # too short, or source-level (`*`) / RPC (`@rpc`)
    entity_id, subject, source = rest[0], rest[2], rest[3:]
    if subject == "*":
        return None  # legacy coarse token (section 5.7)
    about_targets = source[-1] == "@target"
    if about_targets:
        source = source[:-1]
    if not source or "@target" in source:
        return None
    return Publication(entity_id, subject, "/".join(source), about_targets)


def classify_interface(key: str) -> Optional[Interface]:
    """An interface-level liveliness token as an :class:`Interface`, else None.

    The token is `.../@rpc/{interface}/{version}/*/{source_id}`: the `*` stands
    in the procedure slot (section 3.5)."""
    rest = _after_v0(key)
    if not rest or len(rest) < 6 or rest[1] != "@rpc" or rest[4] != "*":
        return None
    return Interface(rest[0], rest[2], rest[3], "/".join(rest[5:]))


def _liveliness_keys(session, selector: str, timeout: float) -> Iterable[str]:
    for reply in session.liveliness().get(selector, timeout=timeout):
        if reply.ok is not None:
            yield str(reply.ok.key_expr)


def list_publications(
    session, realm: str, entity_id: str = "*", timeout: float = DEFAULT_TIMEOUT_S
) -> List[Publication]:
    """Every subject each live source advertises, plain and target-scoped.

    Two queries, because `@target` is verbatim and no pattern returns both
    kinds (section 5.5)."""
    found = set()
    for selector in (
        f"{realm}/@v0/{entity_id}/pubsub/*/**",
        f"{realm}/@v0/{entity_id}/pubsub/*/**/@target/**",
    ):
        for key in _liveliness_keys(session, selector, timeout):
            pub = classify_publication(key)
            if pub is not None:
                found.add(pub)
    return sorted(found)


def list_interfaces(
    session, realm: str, entity_id: str = "*", timeout: float = DEFAULT_TIMEOUT_S
) -> List[Interface]:
    """Every (interface, version) each live source serves."""
    found = set()
    for key in _liveliness_keys(session, f"{realm}/@v0/{entity_id}/@rpc/**", timeout):
        iface = classify_interface(key)
        if iface is not None:
            found.add(iface)
    return sorted(found)


def _rpc_responder(key: str) -> Optional[tuple]:
    """(entity_id, source_id) of a `.../@rpc/configurable/v1/get_config/{src}` key."""
    rest = _after_v0(key)
    if not rest or len(rest) < 6 or rest[1] != "@rpc":
        return None
    return rest[0], "/".join(rest[5:])


def list_platforms(
    session, realm: str, timeout: float = DEFAULT_TIMEOUT_S
) -> List[PlatformInfo]:
    """Every platform on the bus, with its configuration, by asking.

    One wildcard ``configurable/v1.get_config`` query reaches every
    configurable source at once (each replies with its configuration as raw
    JSON). A platform's document carries ``platform_type``; other
    configurable sources answer too and are left out. Live, so no router
    storage is needed, and a platform that is down is simply absent.
    """
    import zenoh

    platforms = []
    selector = f"{realm}/@v0/*/@rpc/configurable/v1/get_config/**"
    for reply in session.get(
        selector, consolidation=zenoh.ConsolidationMode.NONE, timeout=timeout
    ):
        if reply.ok is None:
            continue
        where = _rpc_responder(str(reply.ok.key_expr))
        if where is None:
            continue
        try:
            config = json.loads(reply.ok.payload.to_bytes())
        except (ValueError, UnicodeDecodeError):
            logger.debug("get_config reply from %s is not JSON; skipped", where)
            continue
        if isinstance(config, dict) and "platform_type" in config:
            platforms.append(PlatformInfo(where[0], where[1], config))
    return sorted(platforms, key=lambda p: (p.entity_id, p.source_id))
