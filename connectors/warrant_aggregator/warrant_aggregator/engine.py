"""The warrant engine: a pure state machine over timestamped EntityHealth
messages.

Two rules govern the graph. Propagation: a fired rebuttal withdraws its
claim, and every claim resting on it is requalified — a claim whose
remaining grounds still support it is weakened rather than withdrawn, and
no claim keeps full standing after one of its grounds has gone.
Non-compensatory aggregation: a required ground below its required
standing withdraws the claim; licensed grounds elsewhere cannot offset it.

The burden of proof is asymmetric: downgrades take effect immediately,
upgrades only after the supporting evidence has held for
requalification_hold_s. The hold guards what evidence drives directly,
source claims and the ladder level; derived standings are logic over
already-guarded inputs and follow their grounds immediately. Absent or
stale evidence counts against a claim, never for it.

The engine is deterministic in its inputs: feed() and tick() take explicit
timestamps, so a recorded stream replays to identical output.
"""

from dataclasses import dataclass, field

from warrant_aggregator.model import (
    HEALTH_LEVEL_NAMES,
    HEALTH_ORDER,
    LICENSED,
    STANDING_NAMES,
    WEAKENED,
    WITHDRAWN,
)


@dataclass
class Evidence:
    level_name: str
    detail: str
    t_ns: int


@dataclass
class ClaimState:
    standing: int = WITHDRAWN
    since_ns: int = 0
    fired: list = field(default_factory=list)  # rebuttal dicts, source claims
    grounds: dict = field(default_factory=dict)  # ground -> standing name
    candidate: int | None = None  # pending upgrade
    candidate_since_ns: int = 0


class WarrantEngine:
    def __init__(self, graph, sink):
        """sink(event: dict) receives standing/level/snapshot events."""
        self.graph = graph
        self.sink = sink
        self.evidence = {}  # (source, subject) -> Evidence
        self.states = {name: ClaimState() for name in graph.claims}
        self.level = graph.ladder[-1].name
        self.level_candidate = None
        self.level_candidate_since_ns = 0
        self.last_snapshot_ns = 0

    # -- inputs ----------------------------------------------------------

    def feed(self, t_ns: int, entity_health) -> None:
        for source in entity_health.sources:
            for subject in source.subjects:
                detail = "; ".join(
                    f"{c.name}:{HEALTH_LEVEL_NAMES[c.level]}"
                    + (f" ({c.detail})" if c.detail else "")
                    for c in subject.checks
                )
                self.evidence[(source.name, subject.name)] = Evidence(
                    level_name=HEALTH_LEVEL_NAMES[subject.level],
                    detail=(
                        f"rate {subject.measured_publication_rate_hz:.1f} Hz; {detail}"
                    ),
                    t_ns=t_ns,
                )
        self._evaluate(t_ns)

    def tick(self, t_ns: int) -> None:
        self._evaluate(t_ns)

    # -- evaluation ------------------------------------------------------

    def _rebuttal_fires(self, rebuttal, t_ns):
        """Returns None if the rebuttal does not fire, else a dict."""
        ev = self.evidence.get((rebuttal.source, rebuttal.subject))
        max_age_ns = int(self.graph.evidence_max_age_s * 1e9)
        if ev is None or t_ns - ev.t_ns > max_age_ns:
            return {
                "id": rebuttal.id,
                "description": rebuttal.description,
                "evidence": "no current assessment (absence of evidence "
                "counts against the claim)",
                "evidence_level": "",
            }
        if HEALTH_ORDER[ev.level_name] < HEALTH_ORDER[rebuttal.level_below]:
            return {
                "id": rebuttal.id,
                "description": rebuttal.description,
                "evidence": f"{rebuttal.source}/{rebuttal.subject} "
                f"{ev.level_name}: {ev.detail}",
                "evidence_level": ev.level_name,
            }
        return None

    def _target(self, claim, t_ns):
        """(target standing, fired rebuttals, ground standings)."""

        def state_of(name):
            return self.states[name].standing

        if claim.is_source_claim:
            fired = [f for r in claim.rebuttals if (f := self._rebuttal_fires(r, t_ns))]
            return (WITHDRAWN if fired else LICENSED), fired, {}

        grounds = {}
        satisfied, full = True, True
        for edge in claim.edges:
            standing = state_of(edge.claim)
            grounds[edge.claim] = STANDING_NAMES[standing]
            if standing < edge.requires:
                satisfied = False
            if standing < LICENSED:
                full = False
        if claim.redundancy:
            licensed = 0
            for member in claim.redundancy.members:
                standing = state_of(member)
                grounds[member] = STANDING_NAMES[standing]
                if standing == LICENSED:
                    licensed += 1
                else:
                    full = False
            if licensed < claim.redundancy.min_licensed:
                satisfied = False
        if not satisfied:
            return WITHDRAWN, [], grounds
        return (LICENSED if full else WEAKENED), [], grounds

    def _evaluate(self, t_ns):
        hold_ns = int(self.graph.requalification_hold_s * 1e9)
        for name in self.graph.order:
            claim = self.graph.claims[name]
            state = self.states[name]
            target, fired, grounds = self._target(claim, t_ns)
            state.fired, state.grounds = fired, grounds
            if target < state.standing:
                self._transition(t_ns, name, state, target)
            elif target > state.standing:
                if not claim.is_source_claim:
                    self._transition(t_ns, name, state, target)
                elif state.candidate != target:
                    state.candidate, state.candidate_since_ns = target, t_ns
                elif t_ns - state.candidate_since_ns >= hold_ns:
                    self._transition(t_ns, name, state, target)
            else:
                state.candidate = None

        self._evaluate_level(t_ns, hold_ns)

        snapshot_ns = int(self.graph.snapshot_period_s * 1e9)
        if t_ns - self.last_snapshot_ns >= snapshot_ns:
            self.last_snapshot_ns = t_ns
            self.sink(self._snapshot_event(t_ns))

    def _transition(self, t_ns, name, state, target):
        event = {
            "kind": "standing",
            "t_ns": t_ns,
            "claim": name,
            "from": STANDING_NAMES[state.standing],
            "to": STANDING_NAMES[target],
            "rebuttals_fired": state.fired,
            "grounds": state.grounds,
        }
        state.standing, state.since_ns, state.candidate = target, t_ns, None
        self.sink(event)

    def _rung_met(self, rung):
        for claim, minimum in rung.requires.items():
            if self.states[claim].standing < minimum:
                return False
        if rung.requires_any:
            return any(
                all(self.states[c].standing >= m for c, m in alt.items())
                for alt in rung.requires_any
            )
        return True

    def _evaluate_level(self, t_ns, hold_ns):
        target = self.graph.ladder[-1].name
        for rung in self.graph.ladder:
            if self._rung_met(rung):
                target = rung.name
                break
        names = [r.name for r in self.graph.ladder]
        drop = names.index(target) > names.index(self.level)
        if target == self.level:
            self.level_candidate = None
            return
        if drop:
            self._level_transition(t_ns, target)
        else:
            if self.level_candidate != target:
                self.level_candidate, self.level_candidate_since_ns = target, t_ns
            elif t_ns - self.level_candidate_since_ns >= hold_ns:
                self._level_transition(t_ns, target)

    def _level_transition(self, t_ns, target):
        event = {
            "kind": "level",
            "t_ns": t_ns,
            "from": self.level,
            "to": target,
            "claims": {n: STANDING_NAMES[s.standing] for n, s in self.states.items()},
        }
        self.level, self.level_candidate = target, None
        self.sink(event)

    def _snapshot_event(self, t_ns):
        return {
            "kind": "snapshot",
            "t_ns": t_ns,
            "level": self.level,
            "claims": {
                name: {
                    "standing": STANDING_NAMES[state.standing],
                    "since_ns": state.since_ns,
                    "rebuttals_fired": state.fired,
                    "grounds": state.grounds,
                    "statement": self.graph.claims[name].statement,
                    "warrant": self.graph.claims[name].warrant,
                    "backing": self.graph.claims[name].backing,
                }
                for name, state in self.states.items()
            },
        }
