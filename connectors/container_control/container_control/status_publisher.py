"""Publish the container set as ``container_status``, so consoles stop polling.

The ``list`` RPC answers a question asked now. A monitoring UI asks it forever:
one call per host every fifteen seconds, each costing a listing plus an inspect
per container, whether or not anything moved. This publishes the same answer
when it changes, so the console subscribes instead.

Shape:

    one thread  ->  snapshot  ->  digest  ->  publish if changed, or if due

**Publishing must never take the RPC surface down.** Same rule the log follower
states, for the same reason: the primary job of this process is answering
``list`` / ``start`` / ``stop``, and losing that because the Docker daemon
hiccupped while we were taking a snapshot would be a self-inflicted outage. So
the tick swallows everything and tries again next interval.

A PERIODIC SNAPSHOT, NOT ``client.events()``. The log follower rejected the
events stream because it is a second unbounded blocking stream with the same
no-timeout failure mode, and that argument applies here unchanged -- with the
same latency budget: it accepted ten seconds of latency on log capture, so five
on a state change is well inside what it already bought.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time

import keelson
from keelson.scaffolding import declare_publisher

from . import model
from keelson.payloads.ContainerHost_pb2 import (
    ContainerHostStatus,
    ContainerStatusTrigger,
)

logger = logging.getLogger("container_control.status")

#: Registered locally at startup by
#: ``keelson.add_well_known_subjects_and_proto_definitions`` -- see
#: ``interfaces/subjects.yaml``. One key per HOST; the payload's own comment has
#: the reasoning (a per-container key cannot express removal).
SUBJECT = "container_status"


class ContainerStatusPublisher:
    """Owns the snapshot thread and its publisher."""

    def __init__(
        self,
        backend,
        guard,
        session,
        *,
        base_path: str,
        entity_id: str,
        source_id: str,
        interval_s: float = 5.0,
        heartbeat_s: float = 30.0,
    ):
        self._backend = backend
        self._guard = guard
        self._session = session
        self._base_path = base_path
        self._entity_id = entity_id
        self._source_id = source_id
        self._interval_s = interval_s
        self._heartbeat_s = heartbeat_s

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._publisher = None
        self._sequence = 0
        self._last_digest: bytes | None = None
        self._last_sent_at = 0.0

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        key = keelson.construct_pubsub_key(
            base_path=self._base_path,
            entity_id=self._entity_id,
            subject=SUBJECT,
            source_id=self._source_id,
        )
        # Not session.declare_publisher: this one derives the subject's QoS from
        # the key, which is the whole reason the subject is registered.
        self._publisher = declare_publisher(self._session, key)
        self._thread = threading.Thread(
            target=self._loop, name="status-publish", daemon=True
        )
        self._thread.start()
        logger.info(
            "Publishing container status on: %s (every %.1fs, heartbeat %.1fs)",
            key,
            self._interval_s,
            self._heartbeat_s,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 2.0)
        self._thread = None

    def __enter__(self) -> ContainerStatusPublisher:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- the loop --------------------------------------------------------

    def _snapshot(self) -> list:
        return [
            model.build_container_info(
                s,
                controllable=self._guard.controllable(s.name, s.id),
                removable=self._guard.removable(s.name, s.id),
            )
            for s in sorted(self._backend.list(), key=lambda s: s.name)
        ]

    @staticmethod
    def _digest(infos: list) -> bytes:
        """A change detector over the serialized set.

        Exact, because ``ContainerInfo`` has no field that ticks on its own --
        ``started_at`` is absolute, and there is no uptime -- and no map fields,
        whose serialization order is not stable. ADDING A MAP FIELD BREAKS THIS,
        and it would break it silently, into "never publishes a change". If one
        appears, compare a tuple of fields instead.
        """
        h = hashlib.blake2b(digest_size=16)
        for info in infos:
            h.update(info.SerializeToString(deterministic=True))
        return h.digest()

    def _publish(self, infos: list, trigger: int) -> None:
        message = ContainerHostStatus(
            containers=infos,
            control_enabled=self._guard.control_enabled,
            remove_enabled=self._guard.remove_enabled,
            trigger=trigger,
            sequence=self._sequence,
        )
        message.observed_at.FromNanoseconds(time.time_ns())
        self._publisher.put(keelson.enclose(message.SerializeToString()))
        self._sequence += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                infos = self._snapshot()
                digest = self._digest(infos)
                now = time.monotonic()

                changed = digest != self._last_digest
                # Zenoh pub/sub does not backfill, so without this a subscriber
                # joining during a quiet period waits indefinitely for its first
                # value. It bounds that wait; it does not remove it, which is why
                # the consumer still primes itself with one `list` call.
                due = (now - self._last_sent_at) >= self._heartbeat_s

                if changed or due:
                    self._publish(
                        infos,
                        (
                            ContainerStatusTrigger.CONTAINER_STATUS_TRIGGER_CHANGE
                            if changed
                            else ContainerStatusTrigger.CONTAINER_STATUS_TRIGGER_HEARTBEAT
                        ),
                    )
                    self._last_digest = digest
                    self._last_sent_at = now
            except Exception:
                # Broad on purpose, exactly as the log follower argues: a daemon
                # restart surfaces as a requests/urllib3 error that is neither
                # APIError nor DockerException, and no snapshot failure is worth
                # taking the RPC surface down for. Debug, not warning: a daemon
                # bounce would otherwise fill the log at one line per tick.
                logger.debug("Container status tick failed", exc_info=True)

            self._stop.wait(self._interval_s)
