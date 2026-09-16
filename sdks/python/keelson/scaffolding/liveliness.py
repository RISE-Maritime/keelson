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


def subject_liveliness_keys(
    base_path: str,
    entity_id: str,
    subject: str,
    source_id: str,
    targeted: bool = False,
) -> list:
    """The subject-level token key(s) for one subject.

    One key normally; two for a source that publishes about other entities,
    the second ending in a bare ``@target`` chunk (§5.2).

    The target-scoped key is the plain key with ``@target`` appended, so it
    is a strict prefix of every key the source publishes about a target and
    cannot drift from them — that drift is what made target producers
    undiscoverable in the first place. It carries no target id and no
    wildcard: a ``*`` in a declared token acts as a pattern and would answer
    a query for any concrete target. A target producer commits to subjects,
    never to a roster of targets: a target appearing is a ``put()`` and a
    target disappearing is silence (§2.1.3), so there is no per-target token.
    """
    plain = construct_pubsub_key(base_path, entity_id, subject, source_id)
    keys = [plain]
    if targeted:
        keys.append(f"{plain}/@target")
    return keys


@contextmanager
def declare_pubsub_subject_liveliness(
    session: zenoh.Session,
    base_path: str,
    entity_id: str,
    source_id: str,
    subjects: Iterable[str],
    targeted: bool = False,
):
    """Declare one subject-level liveliness token per subject in
    ``subjects`` — the source's static publishing surface.

    The tokens declare *capability*, not activity: "I am configured to
    publish on this subject; when conditions warrant, data will appear."
    Declare every subject the source can publish, even if the attached
    hardware currently produces no data for some of them; never retract
    on data absence.

    ``targeted=True`` additionally declares the target-scoped form,
    ``.../{subject}/{source_id}/@target``, for a source that publishes
    about *other* entities (§5.2). Both forms are declared, not one: the
    plain token is what every existing discovery query can see, and no
    query can cross ``@target``, so declaring only the target form would
    remove the source from the bus's view entirely. Set it on an AIS, TAK
    or radar-track source; leave it off for anything publishing about the
    entity it runs on.

    Use as a context manager — all tokens are undeclared when the ``with``
    block exits. For runtime-dynamic publishing surfaces (device
    enumeration, config reload) use :class:`PubsubSubjectLivelinessManager`.

    Yields:
        The list of raw Zenoh liveliness tokens.
    """
    tokens = []
    try:
        for subject in subjects:
            for key in subject_liveliness_keys(
                base_path, entity_id, subject, source_id, targeted
            ):
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
        targeted: bool = False,
    ):
        self._session = session
        self._base_path = base_path
        self._entity_id = entity_id
        self._source_id = source_id
        # Beside `source_id` because it is a property of the producing
        # identity, not of any one subject: a source publishes about itself
        # or about others, and a source that did both would be two sources.
        self._targeted = targeted
        self._tokens: dict[str, list] = {}
        self._lock = threading.Lock()

    def add(self, subject: str) -> None:
        """Declare the token(s) for ``subject`` (no-op if already declared).

        Two tokens rather than one when the manager is ``targeted`` — see
        :func:`subject_liveliness_keys`.
        """
        with self._lock:
            if subject in self._tokens:
                return
            declared = []
            for key in subject_liveliness_keys(
                self._base_path,
                self._entity_id,
                subject,
                self._source_id,
                self._targeted,
            ):
                declared.append(self._session.liveliness().declare_token(key))
                logger.debug("Declared subject-level liveliness token: %s", key)
            self._tokens[subject] = declared

    def remove(self, subject: str) -> None:
        """Undeclare the token(s) for ``subject`` (no-op if not declared)."""
        with self._lock:
            tokens = self._tokens.pop(subject, None)
        for token in tokens or ():
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
            tokens = [t for group in self._tokens.values() for t in group]
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
    targeted: bool = False,
):
    """Composite three-tier declaration for the common static case: the
    source-level token, one subject-level token per entry in
    ``pubsub_subjects``, and one interface-level token per
    ``(interface, version)`` in ``rpc_interfaces``.

    ``targeted=True`` says the subjects are published about *other* entities
    — an AIS, TAK or track source — and adds the target-scoped token beside
    each plain one (§5.2). Without it such a source is present on the bus and
    advertises subjects, but nothing distinguishes it from one publishing
    about itself, and the keys it advertises are keys it never writes to.

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
                    session, base_path, entity_id, source_id, subjects, targeted
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
