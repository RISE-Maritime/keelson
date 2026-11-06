"""Claim-graph configuration: loading and validation.

A claim graph declares claims, each carrying the justification for relying
on it. Source claims bind to the bus through rebuttal conditions,
predicates over (source, subject) health levels reported by entity_health;
derived claims rest on other claims through ground edges with required
standings. Standings form a three-valued lattice,
WITHDRAWN < REDUCED < LICENSED, and every claim starts WITHDRAWN:
standing must be positively established.
"""

from dataclasses import dataclass, field

import yaml

WITHDRAWN, REDUCED, LICENSED = 0, 1, 2
STANDING_NAMES = {WITHDRAWN: "WITHDRAWN", REDUCED: "REDUCED", LICENSED: "LICENSED"}
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


# Context facts a condition may name (keelson #414): labels the route carries
# for the leg being sailed, resolved from route_execution's route_ref and
# current leg (warrant_aggregator.context). Values are the enum names without
# their prefix, so `in: [HEAVY]` reads as written.
def _enum_names(enum, prefix):
    return frozenset(
        name.removeprefix(prefix)
        for name in enum.keys()
        if not name.endswith("_UNSPECIFIED")
    )


def _context_facts():
    from keelson.payloads.Route_pb2 import (
        ColregSituation,
        ColregTrafficIntensity,
        OperationalContext,
    )

    return {
        "operational_context": _enum_names(OperationalContext, "OPERATIONAL_CONTEXT_"),
        "traffic_intensity": _enum_names(
            ColregTrafficIntensity, "COLREG_TRAFFIC_INTENSITY_"
        ),
        "colreg_situation": _enum_names(ColregSituation, "COLREG_SITUATION_"),
    }


@dataclass(frozen=True)
class HealthCondition:
    """Holds while the subject's entity_health level orders below a level."""

    source: str
    subject: str
    level_below: str


@dataclass(frozen=True)
class ContextCondition:
    """Holds while a context fact of the leg being sailed is one of `values`."""

    fact: str
    values: frozenset


@dataclass(frozen=True)
class AllOf:
    """Holds while every part holds: a coupled limit (#226)."""

    parts: tuple


@dataclass(frozen=True)
class AnyOf:
    """Holds while at least one part holds."""

    parts: tuple


@dataclass
class Rebuttal:
    id: str
    description: str
    when: object  # HealthCondition | ContextCondition | AllOf | AnyOf


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
    # List of such dicts, of which at least one must hold. None means the
    # rung carries no such constraint; an explicitly empty list is
    # unsatisfiable (any([]) is False), never unconstrained.
    requires_any: list | None


@dataclass
class ClaimGraph:
    claims: dict
    order: list  # topological, grounds before dependents
    ladder: list  # rungs, highest first
    evidence_max_age_s: float
    requalification_hold_s: float
    snapshot_period_s: float
    # The document this graph was built from, as loaded. Kept so `get_config`
    # can answer with what was actually applied rather than a re-serialisation
    # of the dataclasses, which would drop anything this loader ignores.
    spec: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls.from_spec(yaml.safe_load(f))

    @classmethod
    def from_spec(cls, spec):
        """Build and validate a graph from the parsed document.

        The one entry point for both a `--config` file and a `set_config`
        request: JSON is YAML, so a document that arrives over the RPC is
        the same mapping `yaml.safe_load` produces from the file, and every
        ValueError raised here is the text a rejected request replies with.
        """
        if not isinstance(spec, dict):
            # ValueError, not the TypeError ruff prefers (TRY004): every
            # rejection from this loader is a rejected DOCUMENT, and the
            # Configurable handler turns exactly ValueError into the reply
            # error a client reads. A TypeError here would be reported as an
            # internal fault rather than "your document is not a mapping".
            raise ValueError("the claim graph must be a mapping")  # noqa: TRY004
        for key in ("claims", "autonomy_ladder"):
            if key not in spec:
                raise ValueError(f"missing {key}")
        claims = {}
        for name, c in spec["claims"].items():
            rebuttals = [
                Rebuttal(
                    id=r["id"],
                    description=r["description"],
                    when=_condition(r["when"], f"{name}/{r['id']}"),
                )
                for r in c.get("rebuttals", [])
            ]
            grounds = c.get("grounds", {})
            edges = [
                Edge(
                    claim=e["claim"],
                    requires=_standing_value(e["requires"], f"{name}/{e['claim']}"),
                )
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
                    k: _standing_value(v, f"ladder {r['name']}/{k}")
                    for k, v in r.get("requires", {}).items()
                },
                requires_any=(
                    None
                    if "requires_any" not in r
                    else [
                        {
                            k: _standing_value(v, f"ladder {r['name']}/{k}")
                            for k, v in alt.items()
                        }
                        for alt in r["requires_any"]
                    ]
                ),
            )
            for r in spec["autonomy_ladder"]
        ]
        for rung in ladder:
            referenced = list(rung.requires) + [
                k for alt in (rung.requires_any or []) for k in alt
            ]
            for claim in referenced:
                if claim not in claims:
                    raise ValueError(f"ladder rung {rung.name}: unknown claim {claim}")
        floor = ladder[-1]
        if floor.requires or floor.requires_any is not None:
            raise ValueError(
                f"ladder rung {floor.name} is the floor and must be "
                "unconditional: a rung with requirements would be reported "
                "as achieved even when they just evaluated false"
            )
        return cls(
            claims=claims,
            order=order,
            ladder=ladder,
            evidence_max_age_s=_seconds(spec, "evidence_max_age_s"),
            requalification_hold_s=_seconds(spec, "requalification_hold_s"),
            snapshot_period_s=_seconds(spec, "snapshot_period_s"),
            spec=spec,
        )


def _seconds(spec, key):
    value = spec.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{key}: expected a non-negative number of seconds")
    return float(value)


def _standing_value(name, where):
    if name not in STANDING_VALUES:
        raise ValueError(
            f"{where}: unknown standing {name!r} "
            f"(expected one of {sorted(STANDING_VALUES)})"
        )
    return STANDING_VALUES[name]


def _condition(spec, where):
    """A `when:` block: a health condition, a context condition, or all_of /
    any_of over either, nested. Exactly one form per block."""
    if not isinstance(spec, dict):
        raise ValueError(f"{where}: a condition is a mapping")
    forms = [k for k in ("all_of", "any_of", "context") if k in spec]
    if "level_below" in spec or "source" in spec or "subject" in spec:
        forms.append("health")
    if len(forms) != 1:
        raise ValueError(
            f"{where}: a condition is exactly one of all_of, any_of, context "
            f"or a health condition (source, subject, level_below); got {forms or 'none'}"
        )
    form = forms[0]
    if form in ("all_of", "any_of"):
        parts = spec[form]
        if not isinstance(parts, list) or not parts:
            raise ValueError(f"{where}: {form} needs a non-empty list")
        built = tuple(
            _condition(p, f"{where}/{form}[{i}]") for i, p in enumerate(parts)
        )
        return AllOf(built) if form == "all_of" else AnyOf(built)
    if form == "context":
        facts = _context_facts()
        fact = spec["context"]
        if fact not in facts:
            raise ValueError(
                f"{where}: unknown context fact {fact!r} (expected one of {sorted(facts)})"
            )
        values = spec.get("in")
        if not isinstance(values, list) or not values:
            raise ValueError(
                f"{where}: a context condition needs `in:`, a list of values"
            )
        unknown = set(values) - facts[fact]
        if unknown:
            raise ValueError(
                f"{where}: {fact} has no value(s) {sorted(unknown)} "
                f"(expected some of {sorted(facts[fact])})"
            )
        if set(spec) - {"context", "in"}:
            raise ValueError(
                f"{where}: unknown key(s) {sorted(set(spec) - {'context', 'in'})}"
            )
        return ContextCondition(fact, frozenset(values))
    for key in ("source", "subject", "level_below"):
        if key not in spec:
            raise ValueError(f"{where}: a health condition needs {key}")
    return HealthCondition(
        spec["source"], spec["subject"], _health_level(spec["level_below"], where)
    )


def _health_level(name, where):
    if name not in HEALTH_ORDER:
        raise ValueError(
            f"{where}: unknown health level {name!r} "
            f"(expected one of {sorted(HEALTH_ORDER)})"
        )
    return name


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
