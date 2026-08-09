"""Liveliness utilities for Keelson health monitoring.

Keelson uses three orthogonal liveliness tiers (see
protocol-specification.md §5):

- **Source-level** — "this process is present as a producer":
  :func:`declare_source_liveliness`. Mandatory for any process with a
  producing role; forbidden for pure consumers (sinks, recorders,
  bridges), whose visibility is a system-level concern (systemd,
  container health, output artifacts), not a wire concern.
- **Pubsub subject-level** — "this source is configured/wired to publish
  subject X" (capability, not activity):
  :func:`declare_pubsub_subject_liveliness` for static publishing
  surfaces, :class:`PubsubSubjectLivelinessManager` for dynamic ones.
  Tokens are never retracted because data is momentarily absent.
- **RPC interface-level** — "this source serves (interface, version)":
  :func:`declare_rpc_interface_liveliness`, or automatically via
  ``keelson.scaffolding.serve_rpc``.

:func:`declare_liveliness` composes source + subject + interface
declarations for the common static case.
"""

import logging
import threading
from contextlib import contextmanager, ExitStack
from typing import Iterable, Tuple

import zenoh

from keelson import (
    construct_liveliness_key,
    construct_pubsub_key,
    construct_rpc_interface_liveliness_key,
    construct_source_liveliness_key,
)

logger = logging.getLogger(__name__)


@contextmanager
def declare_liveliness_token(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
):
    """Declare the legacy coarse liveliness token for a source.

    .. deprecated:: Use the three-tier declarations instead
       (:func:`declare_source_liveliness` +
       :func:`declare_pubsub_subject_liveliness` /
       :func:`declare_rpc_interface_liveliness`, or the composite
       :func:`declare_liveliness`). Kept for the transition window only.

    Use as a context manager — the token is automatically undeclared when the
    ``with`` block exits.

    Args:
        session: Active Zenoh session.
        base_path: Base path for Keelson keys.
        entity_id: Entity identifier.
        source_id: Source identifier (e.g. ``gnss/0``).

    Yields:
        The raw Zenoh liveliness token.
    """
    key = construct_liveliness_key(base_path, entity_id, source_id)
    raw_token = session.liveliness().declare_token(key)
    try:
        yield raw_token
    finally:
        raw_token.undeclare()


@contextmanager
def declare_source_liveliness(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
):
    """Declare the source-level liveliness token: "this process is present
    on the bus as a producer in some category".

    Declare exactly one per process with a producing role (publishes
    pubsub data and/or serves RPC). Pure consumers must not declare any
    liveliness token.

    Use as a context manager — the token is automatically undeclared when
    the ``with`` block exits.

    Yields:
        The raw Zenoh liveliness token.
    """
    key = construct_source_liveliness_key(base_path, entity_id, source_id)
    raw_token = session.liveliness().declare_token(key)
    logger.debug("Declared source-level liveliness token: %s", key)
    try:
        yield raw_token
    finally:
        raw_token.undeclare()


@contextmanager
def declare_pubsub_subject_liveliness(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
    subjects: Iterable[str],
):
    """Declare one subject-level liveliness token per subject in
    ``subjects`` — the source's static publishing surface.

    The tokens declare *capability*, not activity: "I am configured to
    publish on this subject; when conditions warrant, data will appear."
    Declare every subject the source can publish, even if the attached
    hardware currently produces no data for some of them; never retract
    on data absence.

    Use as a context manager — all tokens are undeclared when the ``with``
    block exits. For runtime-dynamic publishing surfaces (device
    enumeration, config reload) use :class:`PubsubSubjectLivelinessManager`.

    Yields:
        The list of raw Zenoh liveliness tokens.
    """
    tokens = []
    try:
        for subject in subjects:
            key = construct_pubsub_key(base_path, entity_id, subject, source_id)
            tokens.append(session.liveliness().declare_token(key))
            logger.debug("Declared subject-level liveliness token: %s", key)
        yield tokens
    finally:
        for token in tokens:
            try:
                token.undeclare()
            except Exception:
                logger.exception("Failed to undeclare subject-level token")


@contextmanager
def declare_rpc_interface_liveliness(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
    interface: str,
    version: str = "v1",
):
    """Declare the liveliness token for one served RPC
    ``(interface, version)`` pair.

    Under the full-interface rule, holding this token is a claim that
    every procedure of the interface version answers with a typed reply.
    ``keelson.scaffolding.serve_rpc`` declares this token automatically;
    this context manager is for servers not built on ``serve_rpc``.

    Yields:
        The raw Zenoh liveliness token.
    """
    key = construct_rpc_interface_liveliness_key(
        base_path, entity_id, interface, version, source_id
    )
    raw_token = session.liveliness().declare_token(key)
    logger.debug("Declared RPC interface liveliness token: %s", key)
    try:
        yield raw_token
    finally:
        raw_token.undeclare()


class PubsubSubjectLivelinessManager:
    """Stateful subject-level token manager for dynamic-capability sources
    (device enumeration, config reload, replay-file loading).

    Maintains one liveliness token per active subject; ``add``/``remove``
    are idempotent and thread-safe. Call :meth:`close` (or use as a
    context manager) to undeclare all outstanding tokens.
    """

    def __init__(
        self,
        session: zenoh.Session,
        base_path: str,
        entity_id: str,
        source_id: str,
    ):
        self._session = session
        self._base_path = base_path
        self._entity_id = entity_id
        self._source_id = source_id
        self._tokens: dict[str, object] = {}
        self._lock = threading.Lock()

    def add(self, subject: str) -> None:
        """Declare the token for ``subject`` (no-op if already declared)."""
        with self._lock:
            if subject in self._tokens:
                return
            key = construct_pubsub_key(
                self._base_path, self._entity_id, subject, self._source_id
            )
            self._tokens[subject] = self._session.liveliness().declare_token(key)
            logger.debug("Declared subject-level liveliness token: %s", key)

    def remove(self, subject: str) -> None:
        """Undeclare the token for ``subject`` (no-op if not declared)."""
        with self._lock:
            token = self._tokens.pop(subject, None)
        if token is not None:
            try:
                token.undeclare()
            except Exception:
                logger.exception(
                    "Failed to undeclare subject-level token for %s", subject
                )

    def subjects(self) -> set:
        """Currently advertised subjects."""
        with self._lock:
            return set(self._tokens)

    def close(self) -> None:
        """Undeclare all outstanding tokens."""
        with self._lock:
            tokens = list(self._tokens.values())
            self._tokens.clear()
        for token in tokens:
            try:
                token.undeclare()
            except Exception:
                logger.exception("Failed to undeclare subject-level token")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


@contextmanager
def declare_liveliness(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
    pubsub_subjects: Iterable[str] = (),
    rpc_interfaces: Iterable[Tuple[str, str]] = (),
):
    """Composite three-tier declaration for the common static case: the
    source-level token, one subject-level token per entry in
    ``pubsub_subjects``, and one interface-level token per
    ``(interface, version)`` in ``rpc_interfaces``.

    Note: if the process serves RPC via ``keelson.scaffolding.serve_rpc``,
    the interface tokens are already declared there — pass only
    ``pubsub_subjects`` here.
    """
    with ExitStack() as stack:
        stack.enter_context(
            declare_source_liveliness(session, base_path, entity_id, source_id)
        )
        subjects = list(pubsub_subjects)
        if subjects:
            stack.enter_context(
                declare_pubsub_subject_liveliness(
                    session, base_path, entity_id, source_id, subjects
                )
            )
        for interface, version in rpc_interfaces:
            stack.enter_context(
                declare_rpc_interface_liveliness(
                    session, base_path, entity_id, source_id, interface, version
                )
            )
        yield


class LivelinessMonitor:
    """Monitor liveliness tokens and track alive sources.

    Wraps a Zenoh liveliness subscriber to maintain a thread-safe set of
    currently alive key expressions. Optional callbacks are fired on join
    and leave events.

    Args:
        session: Active Zenoh session.
        key_expr: Key expression pattern to monitor (e.g. ``keelson/@v0/**``).
        on_join: Optional callback ``(key_expr: str) -> None`` fired on join.
        on_leave: Optional callback ``(key_expr: str) -> None`` fired on leave.
        history: If True (default), query existing tokens on startup.
    """

    def __init__(
        self,
        session: zenoh.Session,
        key_expr: str,
        on_join=None,
        on_leave=None,
        history=True,
    ):
        self._session = session
        self._key_expr = key_expr
        self._on_join = on_join
        self._on_leave = on_leave
        self._alive: set[str] = set()
        self._lock = threading.Lock()

        self._subscriber = session.liveliness().declare_subscriber(
            key_expr, self._on_event, history=history
        )

    def _on_event(self, sample):
        key = str(sample.key_expr)
        kind = sample.kind

        if kind == zenoh.SampleKind.PUT:
            with self._lock:
                self._alive.add(key)
            if self._on_join is not None:
                try:
                    self._on_join(key)
                except Exception:
                    logger.exception("on_join callback error for %s", key)

        elif kind == zenoh.SampleKind.DELETE:
            with self._lock:
                self._alive.discard(key)
            if self._on_leave is not None:
                try:
                    self._on_leave(key)
                except Exception:
                    logger.exception("on_leave callback error for %s", key)

    def get_alive(self) -> set:
        """Return a copy of the currently alive key expressions."""
        with self._lock:
            return set(self._alive)

    def is_alive(self, key_expr: str) -> bool:
        """Check whether a specific key expression is currently alive."""
        with self._lock:
            return key_expr in self._alive

    def count(self) -> int:
        """Return the number of currently alive sources."""
        with self._lock:
            return len(self._alive)

    def close(self):
        """Undeclare the liveliness subscriber."""
        if self._subscriber is not None:
            self._subscriber.undeclare()
            self._subscriber = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
