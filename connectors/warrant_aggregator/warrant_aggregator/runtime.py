"""The running aggregator: the engine plus everything a reconfiguration has
to swap together.

The bin script used to hold the engine, the policy digest and the clock
bookkeeping as closures over one `with zenoh.open()` block, which was fine
while the graph was fixed for the life of the process. Runtime
reconfiguration over the Configurable RPC changes three of those at once —
graph, engine, digest — and they must change under one lock or a sample
arriving mid-swap is evaluated by the old engine and recorded under the new
digest. Holding them in one object also makes the swap testable without a
Zenoh session: the tests hand in list-appending sinks.

Two sinks, kept separate because they are different outputs: the record
(engine events, published as WarrantRecord) and the determination
(OperationalAuthority). The bin decides what publishing means; this class
decides when.
"""

import json
import threading
import time

from warrant_aggregator.engine import WarrantEngine
from warrant_aggregator.model import ClaimGraph
from warrant_aggregator.wire import (
    operational_authority_from_state,
    policy_config_digest_of_spec,
    validate_ladder_names,
)


class Runtime:
    def __init__(
        self,
        graph: ClaimGraph,
        record_sink,
        authority_sink,
        *,
        policy_id: str,
        clock: str = "hybrid",
        digest: bytes | None = None,
    ):
        """record_sink(event: dict) takes engine events, snapshots already
        stamped with policy identity; authority_sink(msg) takes a built
        OperationalAuthority. clock is "hybrid" or "data", as on the CLI."""
        self.lock = threading.Lock()
        self.policy_id = policy_id
        self.clock = clock
        self.record_sink = record_sink
        self.authority_sink = authority_sink
        self.graph = graph
        self.digest = (
            digest if digest is not None else policy_config_digest_of_spec(graph.spec)
        )
        self.engine = WarrantEngine(graph, self._sink)
        self.last_enclosed_ns = None
        self.last_wall_ns = None
        self.level_dirty = False

    # -- the engine's sink -----------------------------------------------

    def _sink(self, event: dict) -> None:
        if event["kind"] == "snapshot":
            event = {
                **event,
                # Hex in the event so a JSONL debug sink stays plain JSON.
                "policy_config_digest": self.digest.hex(),
                "policy_id": self.policy_id,
            }
        if event["kind"] == "level":
            self.level_dirty = True
        self.record_sink(event)

    # -- clock -----------------------------------------------------------

    def now_ns(self) -> int:
        """The evaluation clock, on the evidence timestamp axis.

        Wall clock enters in exactly one place, and only in hybrid mode:
        between messages, the time elapsed since the last one is added to
        that message's enclosed_at, which is what detects evidence going
        stale. In data mode the clock is the last message's timestamp.
        Before any evidence there is no axis yet, and wall time is all
        there is."""
        if self.last_enclosed_ns is None:
            return time.time_ns()
        if self.clock == "hybrid":
            return self.last_enclosed_ns + (time.time_ns() - self.last_wall_ns)
        return self.last_enclosed_ns

    # -- inputs ----------------------------------------------------------

    def feed(self, enclosed_at: int, entity_health) -> None:
        with self.lock:
            self.last_enclosed_ns = enclosed_at
            self.last_wall_ns = time.time_ns()
            self.engine.feed(enclosed_at, entity_health)
            if self.level_dirty:
                self.level_dirty = False
                self.publish_authority(enclosed_at)

    def tick_and_publish(self) -> None:
        """The periodic step: advance the clock (hybrid) and publish the
        determination. Nothing to evaluate before the first evidence."""
        with self.lock:
            if self.last_enclosed_ns is None:
                return
            now_ns = self.now_ns()
            if self.clock == "hybrid":
                self.engine.tick(now_ns)
            self.level_dirty = False
            self.publish_authority(now_ns)

    def publish_authority(self, t_ns: int) -> None:
        self.authority_sink(
            operational_authority_from_state(
                self.engine, t_ns, self.policy_id, self.digest
            )
        )

    # -- Configurable ----------------------------------------------------

    def get_config(self) -> dict:
        with self.lock:
            return json.loads(
                json.dumps(self.graph.spec)
            )  # a copy the caller may mutate

    @staticmethod
    def validate(new_spec) -> ClaimGraph:
        """The graph a document describes, or a ValueError saying why not.

        model.py raises ValueError for the rules it states and lets a missing
        key surface as KeyError; a request must get one kind of answer, so
        the structural failures are folded into ValueError here."""
        try:
            graph = ClaimGraph.from_spec(new_spec)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"invalid claim graph: {exc!r}") from exc
        validate_ladder_names(graph)
        return graph

    def set_config(self, new_spec) -> None:
        """Replace the graph. Rejected documents change nothing.

        Every claim restarts WITHDRAWN and the level falls to the floor: the
        burden of proof is the same as at startup, and a standing nobody has
        evaluated under the new rules is not published. Evidence already
        seen carries over — facts do, conclusions do not — so the first
        evaluation does not fire every rebuttal on "no current assessment".
        The snapshot that follows is the first record under the new digest.
        """
        new_graph = self.validate(new_spec)
        with self.lock:
            fresh = WarrantEngine(new_graph, self._sink)
            fresh.evidence = dict(self.engine.evidence)
            self.graph = new_graph
            self.engine = fresh
            self.digest = policy_config_digest_of_spec(new_graph.spec)
            if self.last_enclosed_ns is None:
                # No evidence axis yet: the first feed evaluates and snapshots.
                return
            now_ns = self.now_ns()
            fresh.tick(now_ns)
            self.level_dirty = False
            self.publish_authority(now_ns)
