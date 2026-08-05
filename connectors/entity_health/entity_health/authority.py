"""Keelson Layer 2: the vessel's own view of how much autonomy it can carry.

`operational_authority` is documented in subjects.yaml as *the vessel's veto on
accepting remote control* — distinct from `command_authority` (who holds the
conn) and `roc_status` (intent). It is therefore computed here, on the vessel,
from the health this connector already evaluates. A shore station computing it
for itself would invert the safety model: the ROC would be deciding that the
vessel is fit to be remotely controlled.

Scoring
-------
Each source contributes a score, and the composite is their mean:

    NOMINAL   1.0
    DEGRADED  0.5
    CRITICAL  0.0
    INACTIVE  0.0
    UNKNOWN   0.0

**UNKNOWN scores zero rather than being excluded from the mean**, and that is
the whole policy. Averaging only over components that are still reporting looks
more "fair" and is exactly wrong here: a vessel that has lost four of its five
sensors would score 1.0 from the one still talking and declare itself fully
autonomous. Not hearing from a component is not evidence that it is healthy, and
for a message whose purpose is to say "you may rely on me", the absence of
evidence has to count against.

The score → level ladder follows the IMO MASS framing in the issue this
implements.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evaluator import (
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_INACTIVE,
    HEALTH_NOMINAL,
    HEALTH_UNKNOWN,
)

# keelson.OperationalAuthority.AuthorityLevel
AUTHORITY_UNKNOWN = 0
AUTHORITY_MINIMAL_SAFE_MODE = 1
AUTHORITY_SUPERVISED_REMOTE = 2
AUTHORITY_REMOTE_CONTROLLED = 3
AUTHORITY_ASSISTED_AUTONOMOUS = 4
AUTHORITY_FULL_AUTONOMOUS = 5

SCORE_BY_LEVEL = {
    HEALTH_NOMINAL: 1.0,
    HEALTH_DEGRADED: 0.5,
    HEALTH_CRITICAL: 0.0,
    HEALTH_INACTIVE: 0.0,
    HEALTH_UNKNOWN: 0.0,
}

# (minimum composite score, level). Ordered best-first; first match wins.
LADDER = (
    (0.85, AUTHORITY_FULL_AUTONOMOUS),
    (0.65, AUTHORITY_ASSISTED_AUTONOMOUS),
    (0.45, AUTHORITY_REMOTE_CONTROLLED),
    (0.25, AUTHORITY_SUPERVISED_REMOTE),
    (0.0, AUTHORITY_MINIMAL_SAFE_MODE),
)

LEVEL_NAMES = {
    AUTHORITY_UNKNOWN: "UNKNOWN",
    AUTHORITY_MINIMAL_SAFE_MODE: "MINIMAL_SAFE_MODE",
    AUTHORITY_SUPERVISED_REMOTE: "SUPERVISED_REMOTE",
    AUTHORITY_REMOTE_CONTROLLED: "REMOTE_CONTROLLED",
    AUTHORITY_ASSISTED_AUTONOMOUS: "ASSISTED_AUTONOMOUS",
    AUTHORITY_FULL_AUTONOMOUS: "FULL_AUTONOMOUS",
}

# How many failing components to name before summarising the rest.
_MAX_NAMED = 3

_LEVEL_PHRASE = {
    HEALTH_DEGRADED: "degraded",
    HEALTH_CRITICAL: "critical",
    HEALTH_INACTIVE: "inactive",
    HEALTH_UNKNOWN: "not reporting",
}


@dataclass(frozen=True)
class Authority:
    level: int
    composite_score: float
    component_scores: dict[str, float]
    reason: str


def score_for(level: int) -> float:
    """Score one component. An unrecognised level is treated as unknown, i.e. 0."""
    return SCORE_BY_LEVEL.get(level, 0.0)


def level_for(score: float) -> int:
    for minimum, level in LADDER:
        if score >= minimum:
            return level
    return AUTHORITY_MINIMAL_SAFE_MODE


def evaluate_authority(sources) -> Authority:
    """Aggregate `SourceState`s into an OperationalAuthority.

    `sources` is `evaluate_grouped()`'s second return value, so this reuses the
    evaluation already done for `entity_health` rather than repeating it.

    With no sources at all the level is UNKNOWN, not MINIMAL_SAFE_MODE: a
    misconfigured connector that monitors nothing has no opinion to offer, and
    reporting all-stop on the strength of an empty config would be a
    determination it has not actually made.
    """
    if not sources:
        return Authority(
            level=AUTHORITY_UNKNOWN,
            composite_score=0.0,
            component_scores={},
            reason="no components configured",
        )

    component_scores = {s.name: score_for(s.level) for s in sources}
    composite = sum(component_scores.values()) / len(component_scores)

    return Authority(
        level=level_for(composite),
        composite_score=composite,
        component_scores=component_scores,
        reason=_build_reason(sources),
    )


def _build_reason(sources) -> str:
    """Name the components dragging the score down, worst first.

    The operator needs to know *why* authority dropped, not only that it did —
    "gnss_main not reporting; battery degraded" sends someone to the right box.
    """
    failing = [s for s in sources if score_for(s.level) < 1.0]
    if not failing:
        return f"all {len(sources)} components nominal"

    # Worst first: a critical component matters more than a degraded one.
    failing.sort(key=lambda s: (score_for(s.level), s.name))
    named = failing[:_MAX_NAMED]
    parts = [f"{s.name} {_LEVEL_PHRASE.get(s.level, 'unhealthy')}" for s in named]
    remainder = len(failing) - len(named)
    if remainder > 0:
        parts.append(f"and {remainder} more")
    return "; ".join(parts)
