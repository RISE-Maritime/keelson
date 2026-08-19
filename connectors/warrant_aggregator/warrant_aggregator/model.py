"""Claim-graph configuration: loading and validation.

A claim graph declares claims, each carrying the justification for relying
on it. Source claims bind to the bus through rebuttal conditions,
predicates over (source, subject) health levels reported by entity_health;
derived claims rest on other claims through ground edges with required
standings. Standings form a three-valued lattice,
WITHDRAWN < WEAKENED < LICENSED, and every claim starts WITHDRAWN:
standing must be positively established.
"""

from dataclasses import dataclass, field

import yaml

WITHDRAWN, WEAKENED, LICENSED = 0, 1, 2
STANDING_NAMES = {WITHDRAWN: "WITHDRAWN", WEAKENED: "WEAKENED", LICENSED: "LICENSED"}
STANDING_VALUES = {v: k for k, v in STANDING_NAMES.items()}

# EntityHealth levels by proto number, ordered for rebuttal comparison.
HEALTH_LEVEL_NAMES = {
    0: "UNKNOWN",
    1: "INACTIVE",
    2: "CRITICAL",
    3: "DEGRADED",
    4: "NOMINAL",
    5: "NOT_ADVERTISED",
}
HEALTH_ORDER = {
    "UNKNOWN": 0,
    "NOT_ADVERTISED": 0,
    "INACTIVE": 0,
    "CRITICAL": 1,
    "DEGRADED": 2,
    "NOMINAL": 3,
}


@dataclass
class Rebuttal:
    id: str
    description: str
    source: str
    subject: str
    level_below: str  # fires when the subject's level orders below this


@dataclass
class Edge:
    claim: str
    requires: int  # minimum standing of the ground


@dataclass
class Redundancy:
    members: list
    min_licensed: int


@dataclass
class Claim:
    name: str
    tier: str
    statement: str
    warrant: str
    backing: str
    rebuttals: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    redundancy: Redundancy | None = None

    @property
    def is_source_claim(self):
        return bool(self.rebuttals)


@dataclass
class LadderRung:
    name: str
    requires: dict  # claim -> minimum standing (all must hold)
    requires_any: list  # list of such dicts (any may hold), optional


@dataclass
class ClaimGraph:
    claims: dict
    order: list  # topological, grounds before dependents
    ladder: list  # rungs, highest first
    evidence_max_age_s: float
    requalification_hold_s: float
    snapshot_period_s: float

    @classmethod
    def load(cls, path):
        with open(path) as f:
            spec = yaml.safe_load(f)
        claims = {}
        for name, c in spec["claims"].items():
            rebuttals = [
                Rebuttal(
                    id=r["id"],
                    description=r["description"],
                    source=r["when"]["source"],
                    subject=r["when"]["subject"],
                    level_below=r["when"]["level_below"],
                )
                for r in c.get("rebuttals", [])
            ]
            grounds = c.get("grounds", {})
            edges = [
                Edge(claim=e["claim"], requires=STANDING_VALUES[e["requires"]])
                for e in grounds.get("edges", [])
            ]
            redundancy = None
            if "redundancy" in grounds:
                r = grounds["redundancy"]
                redundancy = Redundancy(
                    members=r["members"], min_licensed=r["min_licensed"]
                )
            if rebuttals and (edges or redundancy):
                raise ValueError(
                    f"{name}: source claims carry rebuttals, "
                    "derived claims carry grounds, never both"
                )
            if not rebuttals and not edges and not redundancy:
                raise ValueError(f"{name}: no rebuttals and no grounds")
            claims[name] = Claim(
                name=name,
                tier=c["tier"],
                statement=c["statement"],
                warrant=c["warrant"],
                backing=c["backing"],
                rebuttals=rebuttals,
                edges=edges,
                redundancy=redundancy,
            )
        order = _toposort(claims)
        ladder = [
            LadderRung(
                name=r["name"],
                requires={
                    k: STANDING_VALUES[v] for k, v in r.get("requires", {}).items()
                },
                requires_any=[
                    {k: STANDING_VALUES[v] for k, v in alt.items()}
                    for alt in r.get("requires_any", [])
                ],
            )
            for r in spec["autonomy_ladder"]
        ]
        for rung in ladder:
            referenced = list(rung.requires) + [
                k for alt in rung.requires_any for k in alt
            ]
            for claim in referenced:
                if claim not in claims:
                    raise ValueError(f"ladder rung {rung.name}: unknown claim {claim}")
        return cls(
            claims=claims,
            order=order,
            ladder=ladder,
            evidence_max_age_s=spec["evidence_max_age_s"],
            requalification_hold_s=spec["requalification_hold_s"],
            snapshot_period_s=spec["snapshot_period_s"],
        )


def _toposort(claims):
    order, seen, visiting = [], set(), set()

    def visit(name):
        if name in seen:
            return
        if name in visiting:
            raise ValueError(f"cycle through {name}")
        visiting.add(name)
        c = claims[name]
        deps = [e.claim for e in c.edges]
        if c.redundancy:
            deps += c.redundancy.members
        for d in deps:
            if d not in claims:
                raise ValueError(f"{name}: unknown ground {d}")
            visit(d)
        visiting.discard(name)
        seen.add(name)
        order.append(name)

    for name in claims:
        visit(name)
    return order
