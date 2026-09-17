"""Follow container logs and publish them as ``log_message`` for recording.

The ``logs`` RPC answers a question asked now. This publishes continuously, so
the fleet's MCAP recorder captures container output alongside everything else on
the bus -- which is the only way anyone reads the logs of a container that has
already died on a machine they cannot reach.

Shape:

    one thread per container  ->  a bounded queue  ->  one publisher thread

Followers do nothing but read frames and enqueue lines. A single publisher
drains with a short timeout, so shutdown latency does not depend on whether any
container happens to be talking.

**Publishing must never take the RPC surface down.** The MCAP recorder exits
when it cannot keep up, which is right for a recorder -- a recorder that drops
data is useless. Here the primary job is answering ``list`` / ``start`` /
``stop``, and losing that because one container is spraying stdout would be a
self-inflicted outage. So overload degrades: the queue drops its oldest entries
and the rate cap sheds lines, both leaving a visible trace.
"""

from __future__ import annotations

import fnmatch
import logging
import queue
import re
import threading
import time
from collections.abc import Callable

import keelson
from keelson.payloads.foxglove.Log_pb2 import Log
from keelson.scaffolding import declare_publisher

from . import model

logger = logging.getLogger("container_control.logs")

#: The well-known keelson subject for log lines. Already mapped to
#: ``foxglove.Log`` upstream, with a ``background`` QoS profile assigned -- so
#: the recorder writes a real protobuf schema and Foxglove's Log panel binds to
#: it. Publishing on any other subject would record as an undecodable blob.
SUBJECT = "log_message"

#: A severity token near the start of a line, bounded so it cannot match inside
#: a word or mid-sentence. Anchored deliberately: "no errors found" must stay
#: INFO, or the Log panel goes red and the operator learns to ignore the colour.
_LEVEL_RE = re.compile(
    r"(?:^|[^A-Za-z])(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|FATAL|CRITICAL)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

#: `panic` is ordinary English ("panic buying") where the tokens above are not,
#: so a bare word boundary is not enough for it. Go -- the only thing that emits
#: this at scale -- always writes `panic:`, so require the colon.
_PANIC_RE = re.compile(r"(?:^|[^A-Za-z])PANIC\s*:", re.IGNORECASE)
_LEVEL_SCAN_CHARS = 48

_LEVELS = {
    "TRACE": Log.DEBUG,
    "DEBUG": Log.DEBUG,
    "INFO": Log.INFO,
    "NOTICE": Log.INFO,
    "WARN": Log.WARNING,
    "WARNING": Log.WARNING,
    "ERROR": Log.ERROR,
    "ERR": Log.ERROR,
    "FATAL": Log.FATAL,
    "CRITICAL": Log.FATAL,
}


def sniff_level(text: str) -> int:
    """Guess a log level from the line, defaulting to INFO.

    A heuristic, and documented as one. It exists because ``foxglove.Log.level``
    drives the Log panel's colouring and filtering, and the alternative --
    mapping stderr to ERROR -- is actively wrong: plenty of well-behaved
    programs write everything to stderr because it is unbuffered.
    """
    window = text[:_LEVEL_SCAN_CHARS]
    if _PANIC_RE.search(window):
        return Log.FATAL
    match = _LEVEL_RE.search(window)
    if match is None:
        return Log.INFO
    return _LEVELS.get(match.group(1).upper(), Log.INFO)


class LineBuffer:
    """Reassembles lines from a stream of frames.

    The follow generator yields *frames*, not lines: a frame can end mid-line
    and a line can span several. Feeding frames straight to the line parser
    would split messages arbitrarily, so the tail is held back until its newline
    arrives.
    """

    def __init__(self):
        self._remainder = b""

    def feed(self, frame: bytes) -> list[bytes]:
        data = self._remainder + frame
        parts = data.split(b"\n")
        self._remainder = parts.pop()
        return parts

    def flush(self) -> list[bytes]:
        """Whatever is left when the stream ends, so a final unterminated line
        is not silently dropped."""
        tail, self._remainder = self._remainder, b""
        return [tail] if tail.strip() else []


class RateLimiter:
    """Per-container line budget, refilled once a second.

    A crash-looping container can emit megabytes a second. Excess is dropped --
    but never silently: :meth:`overflow_note` reports the count so the gap shows
    up in the recording. A drop that leaves no trace is worse than the volume.
    """

    def __init__(self, max_lines_per_s: int):
        self.max_lines_per_s = max_lines_per_s
        self._window = 0.0
        self._count = 0
        self._dropped = 0

    def allow(self, now: float) -> bool:
        if self.max_lines_per_s <= 0:
            return True
        if now - self._window >= 1.0:
            self._window = now
            self._count = 0
        self._count += 1
        if self._count <= self.max_lines_per_s:
            return True
        self._dropped += 1
        return False

    def overflow_note(self) -> str | None:
        if not self._dropped:
            return None
        note = f"[keelson] dropped {self._dropped} line(s) -- rate cap of {self.max_lines_per_s}/s"
        self._dropped = 0
        return note


class LogFollower:
    """Owns the follower threads, the queue and the publisher thread."""

    def __init__(
        self,
        backend,
        session,
        *,
        base_path: str,
        entity_id: str,
        source_id: str,
        globs: tuple[str, ...],
        rescan_s: float = 10.0,
        max_lines_per_s: int = 200,
        queue_size: int = 10_000,
        tail: int = 0,
    ):
        self._backend = backend
        self._session = session
        self._base_path = base_path
        self._entity_id = entity_id
        self._source_id = source_id
        self._globs = globs
        self._rescan_s = rescan_s
        self._max_lines_per_s = max_lines_per_s
        self._tail = tail

        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._followers: dict[str, Callable] = {}
        self._publishers: dict[str, object] = {}
        self._lock = threading.Lock()
        self._dropped_by_queue = 0

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        for target, name in (
            (self._publish_loop, "log-publisher"),
            (self._scan_loop, "log-scan"),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info(
            "Following container logs matching: %s (subject %s)",
            ", ".join(self._globs),
            SUBJECT,
        )

    def stop(self) -> None:
        self._stop.set()
        # Unblock every follower: they are parked in a socket read with the
        # timeout disabled, so nothing short of closing the response returns.
        with self._lock:
            closers = list(self._followers.values())
        for close in closers:
            close()
        for thread in self._threads:
            thread.join(timeout=5.0)

    def __enter__(self) -> LogFollower:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- discovery -------------------------------------------------------

    def _matches(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, g) for g in self._globs)

    def _scan_loop(self) -> None:
        """Start followers for new containers; reap finished ones.

        A periodic rescan rather than ``client.events()``: the events stream is
        more responsive but is a second unbounded blocking stream with the same
        no-timeout failure mode, and ten seconds of latency on a log stream is
        not worth another thing that can wedge.
        """
        while not self._stop.is_set():
            try:
                names = [s.name for s in self._backend.list() if self._matches(s.name)]
            except Exception:
                logger.debug("Log follower rescan failed", exc_info=True)
                names = []

            with self._lock:
                known = set(self._followers)
            for name in names:
                if name not in known:
                    self._spawn(name)

            self._stop.wait(self._rescan_s)

    def _spawn(self, name: str) -> None:
        thread = threading.Thread(
            target=self._follow, args=(name,), name=f"log-follow-{name}", daemon=True
        )
        with self._lock:
            # Registered before the thread starts so a rescan cannot double-spawn.
            self._followers[name] = lambda: None
        thread.start()
        self._threads.append(thread)

    # -- following -------------------------------------------------------

    def _follow(self, name: str) -> None:
        """One container, until shutdown or until it stops existing."""
        limiter = RateLimiter(self._max_lines_per_s)
        since: float | None = None
        buffer = LineBuffer()

        while not self._stop.is_set():
            try:
                frames, close, _snapshot = self._backend.follow_logs(
                    name, since=since, tail=self._tail
                )
                with self._lock:
                    self._followers[name] = close

                for frame in frames:
                    if self._stop.is_set():
                        break
                    for chunk in buffer.feed(frame):
                        since = self._emit(name, chunk, limiter, since)
                for chunk in buffer.flush():
                    since = self._emit(name, chunk, limiter, since)

            except Exception as exc:
                if self._stop.is_set():
                    break
                # Deliberately broad. A daemon restart surfaces as a
                # requests/urllib3 error that is neither APIError nor
                # DockerException, so the narrow handlers elsewhere in this
                # package would let it kill the thread.
                logger.debug("Log follow for %s failed: %s", name, exc)

            if self._stop.is_set():
                break

            # The generator also ends cleanly when the container stops, which is
            # indistinguishable from a dropped connection without asking. If it
            # is gone for good the next rescan simply will not re-list it.
            if not self._still_present(name):
                logger.info("Stopped following %s: no longer present", name)
                break
            self._stop.wait(2.0)

        with self._lock:
            self._followers.pop(name, None)

    def _still_present(self, name: str) -> bool:
        try:
            self._backend.get(name)
        except Exception:
            return False
        return True

    def _emit(self, name: str, chunk: bytes, limiter: RateLimiter, since: float | None):
        line = model.parse_log_line(chunk, 0)
        if line is None:
            return since

        now = time.time()
        if not limiter.allow(now):
            return since

        note = limiter.overflow_note()
        if note is not None:
            self._enqueue(name, note, Log.WARNING, now)

        stamp = line.time.ToNanoseconds() / 1e9 if line.HasField("time") else now
        self._enqueue(name, line.text, sniff_level(line.text), stamp)
        # Resume from the last line seen. Docker's `since` is inclusive at second
        # granularity, so a reconnect re-sees the boundary line; one duplicated
        # line beats a gap.
        return stamp

    def _enqueue(self, name: str, text: str, level: int, stamp: float) -> None:
        item = (name, text, level, stamp)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Drop the OLDEST: on a burst the recent lines are the ones worth
            # keeping, and blocking here would stall the follower thread.
            try:
                self._queue.get_nowait()
                self._dropped_by_queue += 1
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._dropped_by_queue += 1

    # -- publishing ------------------------------------------------------

    def _publisher_for(self, container: str):
        publisher = self._publishers.get(container)
        if publisher is None:
            key = keelson.construct_pubsub_key(
                base_path=self._base_path,
                entity_id=self._entity_id,
                subject=SUBJECT,
                # One MCAP channel per container: the recorder makes a channel
                # per full key, so this is what lets an operator toggle a single
                # container's logs in Foxglove instead of filtering a merged
                # firehose.
                source_id=f"{self._source_id}/{container}",
            )
            # Not session.declare_publisher: this one derives the subject's QoS
            # profile (background: DATA_LOW/DROP/RELIABLE) from the key.
            publisher = declare_publisher(self._session, key)
            self._publishers[container] = publisher
            logger.info("Publishing logs for %s on %s", container, key)
        return publisher

    def _publish_loop(self) -> None:
        while not self._stop.is_set():
            try:
                container, text, level, stamp = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                payload = Log(level=level, message=text, name=container)
                payload.timestamp.FromNanoseconds(int(stamp * 1e9))
                self._publisher_for(container).put(
                    keelson.enclose(payload.SerializeToString())
                )
            except Exception:
                logger.debug(
                    "Failed to publish a log line for %s", container, exc_info=True
                )

        if self._dropped_by_queue:
            logger.warning(
                "Dropped %d log line(s) to keep up; the RPC surface was kept "
                "answering in preference to the log stream",
                self._dropped_by_queue,
            )
