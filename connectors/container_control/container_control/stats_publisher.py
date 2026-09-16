"""Publish what each container is costing, as ``container_stats``.

``container_status`` answers "what is running". This answers "what is it
costing" -- CPU, memory, network, block I/O, and the CFS throttling that says a
limit is biting. Off unless ``--publish-stats`` is given.

Shape::

    one thread  ->  sweep every running container  ->  publish, every tick

A SEQUENTIAL SWEEP FROM ONE THREAD, not a stream per container. The log follower
holds one blocking connection per container because a log line arrives when it
arrives; a counter can simply be read. ``backend.stats()`` reads the whole host
in about a tenth of a second, so the thread-per-container machinery -- the
connection pool ceiling, the closers that unblock a parked socket read at
shutdown -- buys nothing here and is not repeated.

NO CHANGE DETECTION, WHICH IS THE ONE PLACE THIS DEPARTS FROM
:mod:`status_publisher`. Container state is a step function, so suppressing
repeats there is what lets a subscriber trust that a message means something
moved. Utilisation changes on every sample by definition: a series with its
repeats suppressed is a series with holes in it, and a consumer could not tell a
quiet container from a stalled publisher. So every tick publishes.

**Publishing must never take the RPC surface down.** Same rule, same reason: the
primary job of this process is answering ``list`` / ``start`` / ``stop``, and
losing that because the Docker daemon hiccupped mid-sweep would be a
self-inflicted outage. The tick swallows everything and tries again.
"""

from __future__ import annotations

import logging
import threading
import time

import keelson
from keelson.scaffolding import declare_publisher

from . import model
from keelson.payloads.ContainerHost_pb2 import ContainerHostStats

logger = logging.getLogger("container_control.stats")

#: Registered locally at startup alongside ``container_status`` -- see
#: ``interfaces/subjects.yaml``. One key per HOST, for the reason that file's
#: sibling subject gives.
SUBJECT = "container_stats"


class ContainerStatsPublisher:
    """Owns the sweep thread, its publisher, and one tick of history."""

    def __init__(
        self,
        backend,
        session,
        *,
        base_path: str,
        entity_id: str,
        source_id: str,
        globs=("*",),
        interval_s: float = 10.0,
    ):
        self._backend = backend
        self._session = session
        self._base_path = base_path
        self._entity_id = entity_id
        self._source_id = source_id
        self._globs = tuple(globs) or ("*",)
        self._interval_s = interval_s

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._publisher = None
        self._sequence = 0
        #: Last tick's counters, KEYED BY CONTAINER ID. See _sweep.
        self._previous: dict[str, model.StatsSample] = {}
        self._warned = False

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
            target=self._loop, name="stats-publish", daemon=True
        )
        self._thread.start()
        logger.info(
            "Publishing container stats on: %s (every %.1fs, matching: %s)",
            key,
            self._interval_s,
            ", ".join(self._globs),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 2.0)
        self._thread = None

    def __enter__(self) -> ContainerStatsPublisher:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- the loop --------------------------------------------------------

    def _sweep(self) -> list:
        """One reading of every matching running container.

        ``running_only`` is a correctness requirement, not a filter preference: a
        stopped container's stats call still answers 200, with an all-empty body
        that would publish as "0% CPU, 0 bytes" -- indistinguishable from an idle
        one. (:func:`model.build_resource_usage` refuses that body too, which
        covers the container that stops between this listing and its own call.)
        """
        entries = []
        samples: dict[str, model.StatsSample] = {}

        snapshots = [
            s
            for s in self._backend.list(running_only=True)
            if model.matches_any(s.name, self._globs)
        ]
        for snapshot in sorted(snapshots, key=lambda s: s.name):
            try:
                raw = self._backend.stats(snapshot.id)
            except Exception:
                # A container that went away mid-sweep costs its own row and
                # nothing else. Losing the other seven to it would be the
                # failure mode this whole tick is written to avoid.
                logger.debug("Stats for %s failed", snapshot.name, exc_info=True)
                continue

            usage, sample = model.build_resource_usage(
                snapshot,
                raw,
                self._previous.get(snapshot.id),
                # Read per container rather than once per sweep: the sweep is
                # sequential, so each container really was sampled at its own
                # instant, and the rates divide by exactly the window they
                # covered.
                monotonic_s=time.monotonic(),
            )
            if usage is None or sample is None:
                continue
            entries.append(usage)
            samples[snapshot.id] = sample

        # THE CACHE IS REAPED BY BEING REBUILT. A container absent from this
        # sweep is simply absent from the new dict, so it cannot grow without
        # bound and there is no separate reaper to forget about. (The log
        # follower needs an explicit pop because its threads outlive a scan;
        # this loop re-enumerates everything every tick, so reaping is free.)
        #
        # KEYED BY ID, NOT NAME. `docker compose up --force-recreate` gives the
        # new container the same name with every counter back at zero, and
        # differencing a fresh container against a dead one's totals would
        # publish a spike that never happened. The id changing is exactly the
        # signal that there is nothing to difference against.
        #
        # Assigned only on a sweep that got this far: a tick that raised inside
        # backend.list() leaves the previous samples in place, so the next good
        # tick still has something to difference against instead of blanking
        # every rate on the host.
        self._previous = samples
        return entries

    def _publish(self, entries: list) -> None:
        message = ContainerHostStats(containers=entries, sequence=self._sequence)
        message.observed_at.FromNanoseconds(time.time_ns())
        self._publisher.put(keelson.enclose(message.SerializeToString()))
        self._sequence += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                started = time.monotonic()
                entries = self._sweep()
                self._publish(entries)

                elapsed = time.monotonic() - started
                if elapsed > 0.5 * self._interval_s:
                    # The one way a sequential sweep goes wrong: enough
                    # containers that it stops fitting inside the interval.
                    # Said out loud before it becomes a tick that never
                    # finishes.
                    logger.warning(
                        "Container stats sweep took %.2fs of a %.1fs interval (%d containers)",
                        elapsed,
                        self._interval_s,
                        len(entries),
                    )
            except Exception:
                if not self._warned:
                    # WARNING once, then debug. --publish-stats is opt-in, so an
                    # operator who typed it and got silence has no thread to
                    # pull -- an API too old for one_shot, or a socket that
                    # cannot be read, surfaces here. Debug thereafter for the
                    # reason the status publisher is debug throughout: a daemon
                    # bounce would otherwise fill the log at a line per tick.
                    self._warned = True
                    logger.warning(
                        "Container stats tick failed; will keep trying", exc_info=True
                    )
                else:
                    logger.debug("Container stats tick failed", exc_info=True)

            self._stop.wait(self._interval_s)
