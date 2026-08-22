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

    NOMINAL         1.0
    DEGRADED        0.5
    CRITICAL        0.0
    INACTIVE        0.0
    UNKNOWN         0.0
    NOT_ADVERTISED  excluded from the mean entirely

**UNKNOWN scores zero rather than being excluded from the mean**, and that is
the whole policy. Averaging only over components that are still reporting looks
more "fair" and is exactly wrong here: a vessel that has lost four of its five
sensors would score 1.0 from the one still talking and declare itself fully
autonomous. Not hearing from a component is not evidence that it is healthy, and
for a message whose purpose is to say "you may rely on me", the absence of
evidence has to count against.

**NOT_ADVERTISED is the one exclusion, and it is not an exception to that
argument** — it is outside its scope. UNKNOWN is a statement about the vessel
(a component that should be talking is silent); NOT_ADVERTISED is a statement
about *this monitor's own config* (it was pointed at a subject the source never
claims). One is evidence about the thing being judged, the other is not
evidence at all, and averaging a config typo into a vessel's declared autonomy
just makes the number mean less. `worst()` in evaluator.py excludes it from
health rollups on the same grounds.

Coverage
--------
A level says how bad things were; it cannot also say how much was looked at.
`worst()` rolls a source with four of five subjects UNKNOWN and one NOMINAL up
to NOMINAL — correctly, since UNKNOWN must never mask a CRITICAL sibling — and
scoring that 1.0 defeats the policy above one level down: the vessel that lost
four of five sensors declares full autonomy after all, just from inside a
single source rather than across them.

So each source is discounted by the fraction of its watched subjects that
reached a verdict:

    effective = score_for(source.level) * coverage_fraction

The composite is the mean of the effective scores, one vote per source. Equal
source weighting is deliberate: averaging over subjects instead would make the
number of diagnostics a source happens to expose into a safety weight, so a
source publishing ten diagnostics would outvote a failed one publishing a
single fix.

**This multiplication is a Keelson policy choice, not a calibrated
probability.** It is not a standards-mandated formula, and `coverage_fraction`
is not IEC 61508 diagnostic coverage — it is simply the proportion of expected
observations that produced a determinate result. It is chosen because it is
monotone in both arguments, keeps sources equally weighted, states the burden
of proof explicitly, and leaves `worst()` untouched.

The score → level ladder follows the IMO MASS framing in the issue this
implements.
"""

from __future__ import annotations

from dataclasses import dataclass

from .levels import (
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_INACTIVE,
    HEALTH_NOMINAL,
    HEALTH_NOT_ADVERTISED,
    HEALTH_UNKNOWN,
    parse_level,
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

# Levels that are excluded from the composite outright rather than scored.
#
# NOT_ADVERTISED is a *watch-config* error — the operator pointed this monitor
# at a subject the source never claims (typo'd subject name, wrong source_id) —
# not a statement about the vessel. `worst()` in evaluator.py already excludes
# it from health rollups for exactly that reason, and it must be excluded here
# too: leaving it to fall through to the 0.0 default silently re-included the
# level evaluator.py deliberately drops, so one typo dragged a vessel whose
# every device was healthy down a whole authority level (six sources, five
# NOMINAL, one typo'd → 0.833 → ASSISTED instead of FULL).
#
# It neither scores nor dilutes: the source leaves the mean entirely. That is
# not the same as scoring it 1.0 — an unassessable source contributes no
# evidence in either direction, and `reason` still names it so the operator can
# find the typo.
UNSCORED_LEVELS = frozenset({HEALTH_NOT_ADVERTISED})

# Levels that count as *assessed* — a determinate verdict was reached about
# the subject, whatever that verdict was.
#
# A known failure is evidence, not missing evidence. CRITICAL and INACTIVE
# already score 0.0 through SCORE_BY_LEVEL; they must not *also* reduce
# coverage, or a confirmed dead sensor would be punished twice while a silent
# one is punished once. Only UNKNOWN reduces coverage, because only UNKNOWN
# means nothing was learned.
ASSESSED_LEVELS = frozenset(
    {HEALTH_NOMINAL, HEALTH_DEGRADED, HEALTH_CRITICAL, HEALTH_INACTIVE}
)

# (minimum composite score, level). Ordered best-first; first match wins.
LADDER = (
    (0.85, AUTHORITY_FULL_AUTONOMOUS),
    (0.65, AUTHORITY_ASSISTED_AUTONOMOUS),
    (0.45, AUTHORITY_REMOTE_CONTROLLED),
    (0.25, AUTHORITY_SUPERVISED_REMOTE),
    (0.0, AUTHORITY_MINIMAL_SAFE_MODE),
)

# How far past a threshold the score must travel before the level follows it.
#
# Without this the ladder is a bare comparison re-evaluated at the publish rate,
# and a vessel sitting on a boundary changes its declared autonomy every tick.
# The composite moves in steps of 1/(2N) as one of N sources flips
# NOMINAL <-> DEGRADED — ~0.042 at the twelve sources a SITL drone reports.
# Whether that crosses a threshold depends on where the rest of the fleet's
# health has already put the score: a vessel with everything nominal steps
# 1.0 <-> 0.958 and never leaves FULL_AUTONOMOUS, but one already carrying a
# couple of degraded sensors sits close to a boundary, and there a single
# flapping sensor moves the declared level once per second.
#
# An operator watching authority oscillate between ASSISTED_AUTONOMOUS and
# FULL_AUTONOMOUS learns nothing except to stop trusting the display, and any
# consumer keyed on the level (an ROC deciding whether to accept control) gets
# whipsawed with it. The margin makes the level sticky: it takes a real move,
# not jitter, to change what the vessel claims it can do.
HYSTERESIS_MARGIN = 0.05


# The three values above are the *tunable* policy: what a health level is
# worth, where the ladder's rungs sit, and how much margin the burden-of-proof
# asymmetry demands. They are data, and a deployment may set them.
#
# UNSCORED_LEVELS and ASSESSED_LEVELS deliberately are NOT here. Those encode
# what a level *means* — a watch-config error is not a fact about the vessel, a
# known failure is evidence rather than missing evidence — and both comments
# above document a specific bug that making them configurable would let an
# operator recreate. The test is whether a value is a policy choice or a
# semantic invariant, and it points opposite ways for two things that look
# alike in the source.
@dataclass(frozen=True)
class Policy:
    """The composite policy: the arithmetic between health and authority.

    Passed in rather than read from module state, for the same reason
    `previous_level` is (see `evaluate_authority`): a policy bug is a
    configuration bug, and a configuration is only easy to write a test for
    when the caller owns it.
    """

    score_by_level: dict
    ladder: tuple
    hysteresis_margin: float

    @classmethod
    def from_config(cls, section: dict | None) -> "Policy":
        """Build from a config `authority_policy` section; absent keys default.

        A partial section is legal and means "the shipped policy, with these
        changes" — an operator retuning one threshold should not have to
        restate the whole ladder and risk a transcription error in the part
        they did not mean to touch.
        """
        section = section or {}

        scores = dict(SCORE_BY_LEVEL)
        for name, value in (section.get("score_by_level") or {}).items():
            scores[parse_level(name)] = float(value)

        rungs = section.get("ladder")
        if rungs is None:
            ladder = LADDER
        else:
            by_name = {v: k for k, v in LEVEL_NAMES.items()}
            parsed = []
            for rung in rungs:
                name = rung["level"]
                if name not in by_name:
                    raise ValueError(
                        f"authority_policy.ladder: unknown level {name!r} "
                        f"(expected one of {sorted(by_name)})"
                    )
                parsed.append((float(rung["min_score"]), by_name[name]))
            # Best-first is a precondition of `_bare_level_for` and of the
            # climb branch in `level_for`, which take the first match as the
            # highest. Sorting here means a config author cannot break that
            # invariant by listing the rungs in a reasonable-looking order.
            ladder = tuple(sorted(parsed, reverse=True))

        margin = float(section.get("hysteresis_margin", HYSTERESIS_MARGIN))
        return cls(score_by_level=scores, ladder=ladder, hysteresis_margin=margin)


LEVEL_NAMES = {
    AUTHORITY_UNKNOWN: "UNKNOWN",
    AUTHORITY_MINIMAL_SAFE_MODE: "MINIMAL_SAFE_MODE",
    AUTHORITY_SUPERVISED_REMOTE: "SUPERVISED_REMOTE",
    AUTHORITY_REMOTE_CONTROLLED: "REMOTE_CONTROLLED",
    AUTHORITY_ASSISTED_AUTONOMOUS: "ASSISTED_AUTONOMOUS",
    AUTHORITY_FULL_AUTONOMOUS: "FULL_AUTONOMOUS",
}

# The shipped policy: what the connector uses when no config section overrides it.
DEFAULT_POLICY = Policy(
    score_by_level=dict(SCORE_BY_LEVEL),
    ladder=LADDER,
    hysteresis_margin=HYSTERESIS_MARGIN,
)

# How many failing components to name before summarising the rest.
_MAX_NAMED = 3

_LEVEL_PHRASE = {
    HEALTH_DEGRADED: "degraded",
    HEALTH_CRITICAL: "critical",
    HEALTH_INACTIVE: "inactive",
    HEALTH_UNKNOWN: "not reporting",
    HEALTH_NOT_ADVERTISED: "not advertised",
}


@dataclass(frozen=True)
class SourceAssessment:
    """One source's contribution, with the arithmetic left visible.

    `effective_score` is `health_score * coverage_fraction`, and keeping all
    three means an operator can tell 0.5 "half the subsystems are degraded"
    apart from 0.5 "everything we could see was fine and we could see half of
    it" — which the composite alone cannot express.
    """

    name: str
    level: int
    health_score: float
    coverage_fraction: float
    effective_score: float
    participates: bool


# Why a requirement is capping authority. Mirrors
# keelson.AuthorityConstraint.Cause; duplicated so this module stays importable
# without the generated protobuf, like the HEALTH_* levels in evaluator.py.
CAUSE_UNSPECIFIED = 0
CAUSE_DEGRADED = 1
CAUSE_CRITICAL = 2
CAUSE_INACTIVE = 3
CAUSE_UNKNOWN = 4
CAUSE_NOT_ADVERTISED = 5
CAUSE_CONFIGURATION_INVALID = 6

_CAUSE_BY_LEVEL = {
    HEALTH_DEGRADED: CAUSE_DEGRADED,
    HEALTH_CRITICAL: CAUSE_CRITICAL,
    HEALTH_INACTIVE: CAUSE_INACTIVE,
    HEALTH_UNKNOWN: CAUSE_UNKNOWN,
    HEALTH_NOT_ADVERTISED: CAUSE_NOT_ADVERTISED,
}


@dataclass(frozen=True)
class Constraint:
    """A prerequisite currently limiting authority.

    Non-compensatory: this ceiling holds however healthy everything else is.
    `invalidates` marks the constraints that do not merely cap the vessel but
    destroy the determination — the monitor cannot see the requirement at all,
    so it has no basis for saying what is permitted.
    """

    component: str
    subject: str | None
    cause: int
    cap_score: float
    invalidates: bool = False


@dataclass(frozen=True)
class Authority:
    level: int
    composite_score: float
    component_scores: dict[str, float]
    reason: str
    # composite_score after every ceiling. None when no valid determination
    # could be made — distinct from 0.0, which is an ordinary "all stop".
    authority_score: float | None = None
    assessments: tuple = ()
    constraints: tuple = ()


def score_for(level: int, policy: Policy = None) -> float:
    """Score one component. An unrecognised level is treated as unknown, i.e. 0."""
    return (policy or DEFAULT_POLICY).score_by_level.get(level, 0.0)


def is_scored(level: int) -> bool:
    """Whether this level contributes to the composite at all. See UNSCORED_LEVELS."""
    return level not in UNSCORED_LEVELS


def coverage_for(subjects) -> float | None:
    """What fraction of a source's watched subjects actually reached a verdict.

    `subjects` is a `SourceState.subjects` list — one entry per *configured*
    watch, so the denominator comes from the config file and never from what
    happens to be advertised on the bus. A component cannot raise the vessel's
    authority by disappearing.

    NOT_ADVERTISED subjects leave the denominator entirely, for the same reason
    NOT_ADVERTISED sources leave the composite (see UNSCORED_LEVELS): a watch
    pointed at a subject the source never claims is a fact about this monitor's
    config, and it must not count for or against the vessel at either level.

    Returns None when nothing is left to measure — every watched subject was a
    config error. The caller must treat that as "does not participate", never
    as full coverage; defaulting to 1.0 would turn a wholly misconfigured
    source into a perfectly healthy one.
    """
    eligible = [q for q in subjects if is_scored(q.level)]
    if not eligible:
        return None
    assessed = sum(1 for q in eligible if q.level in ASSESSED_LEVELS)
    return assessed / len(eligible)


def _bare_level_for(score: float, policy: Policy = None) -> int:
    """The ladder with no memory — where the score alone puts the vessel."""
    for minimum, level in (policy or DEFAULT_POLICY).ladder:
        if score >= minimum:
            return level
    return AUTHORITY_MINIMAL_SAFE_MODE


def _threshold_for(level: int, policy: Policy = None) -> float:
    """The minimum score that `level` requires."""
    for minimum, candidate in (policy or DEFAULT_POLICY).ladder:
        if candidate == level:
            return minimum
    return 0.0


# Thresholds and the margin are decimal literals, so their sum is not the
# decimal number it reads as: `0.65 + 0.05 == 0.7000000000000001`. Compared
# bare, a vessel parked at exactly 0.70 would never climb to
# ASSISTED_AUTONOMOUS while the arithmetically identical 0.45 + 0.05 boundary
# does climb at exactly 0.50. Every ladder comparison goes through this so the
# boundaries mean what they say in both directions.
_EPS = 1e-9


def _at_least(score: float, threshold: float) -> bool:
    """`score >= threshold`, immune to the last-bit error in `threshold`."""
    return score >= threshold - _EPS


def level_for(
    score: float, previous_level: int | None = None, policy: Policy = None
) -> int:
    """Where the score puts the vessel, given where it already was.

    Asymmetric on purpose, and the asymmetry is the safety argument:

    * To climb, the score must clear the higher threshold **by the margin**.
      Claiming more autonomy is the direction that can hurt someone, so it is
      the direction made harder to take on marginal evidence.
    * To fall, the score must drop below the current level's threshold **by the
      margin** too — otherwise a vessel hovering on a boundary would still
      chatter downward every tick, which is the same display problem and also
      trains operators to ignore a degradation that is real.

    The asymmetry is a **burden-of-proof rule — the burden sits on whichever
    claim licenses more action — not display smoothing.** Read as smoothing it
    looks like an inconsistency worth tidying up, and symmetrising the margin
    deletes the principle while leaving the arithmetic looking neater.

    With no `previous_level` — the first tick after start, or a caller that
    keeps no state — this is exactly the old bare ladder, so behaviour on a
    cold start is unchanged.
    """
    policy = policy or DEFAULT_POLICY
    bare = _bare_level_for(score, policy)
    if previous_level is None or previous_level == AUTHORITY_UNKNOWN:
        return bare
    if bare == previous_level:
        return previous_level

    if bare > previous_level:
        # Climbing: rise to the HIGHEST level whose threshold the score clears
        # by the margin — not to `bare` or nowhere. Gating on `bare`'s own
        # threshold alone leaves a dead zone that never resolves: a vessel
        # recovering to a steady 0.87 has bare == FULL_AUTONOMOUS, misses
        # 0.85 + 0.05, and holds MINIMAL_SAFE_MODE forever, never climbing even
        # to ASSISTED_AUTONOMOUS which 0.87 clears comfortably. It under-claims
        # in silence, which is the same operator-distrust failure the margin
        # exists to prevent.
        #
        # LADDER is ordered best-first, so the first match IS the highest, and
        # it can never exceed `bare`: clearing `threshold + margin` implies
        # clearing `threshold`.
        for minimum, level in policy.ladder:
            if level > previous_level and _at_least(
                score, minimum + policy.hysteresis_margin
            ):
                return level
        return previous_level

    # Falling: hold until clearly below what the current level requires.
    if not _at_least(
        score, _threshold_for(previous_level, policy) - policy.hysteresis_margin
    ):
        return bare
    return previous_level


def evaluate_authority(
    sources,
    previous_level: int | None = None,
    essential=None,
    policy: Policy = None,
) -> Authority:
    """Aggregate `SourceState`s into an OperationalAuthority.

    `sources` is `evaluate_grouped()`'s second return value, so this reuses the
    evaluation already done for `entity_health` rather than repeating it.

    `previous_level` is the level this connector published on its last tick, and
    it is the only state the determination carries. Passing it in rather than
    holding it in a module global keeps this function pure and testable: a
    hysteresis bug is a sequence bug, and a sequence is only easy to write a
    test for when the caller owns the state.

    With no sources at all the level is UNKNOWN, not MINIMAL_SAFE_MODE: a
    misconfigured connector that monitors nothing has no opinion to offer, and
    reporting all-stop on the strength of an empty config would be a
    determination it has not actually made. A config whose every source is
    unscorable (see UNSCORED_LEVELS) reaches the same place by the same
    argument: nothing was assessed, so there is nothing to claim.
    """
    policy = policy or DEFAULT_POLICY
    if not sources:
        return Authority(
            level=AUTHORITY_UNKNOWN,
            composite_score=0.0,
            component_scores={},
            reason="no components configured",
        )

    assessments = [_assess(s, policy) for s in sources]
    scored = [a for a in assessments if a.participates]
    unscored = [a for a in assessments if not a.participates]
    constraints = tuple(evaluate_constraints(sources, essential, policy))

    if not scored:
        return Authority(
            level=AUTHORITY_UNKNOWN,
            composite_score=0.0,
            component_scores={},
            reason=_build_reason(scored, unscored, constraints),
            authority_score=None,
            assessments=tuple(assessments),
            constraints=constraints,
        )

    # Only the participating sources appear in the map — that is what excluding
    # them from the composite means. `reason` still names the rest.
    component_scores = {a.name: a.effective_score for a in scored}
    composite = sum(component_scores.values()) / len(component_scores)
    reason = _build_reason(scored, unscored, constraints)

    if any(c.invalidates for c in constraints):
        # Not "the minimum is permitted" — "we cannot say what is permitted".
        # Reported as UNKNOWN with no authority_score so a consumer cannot
        # mistake an unreadable prerequisite for an absent one.
        return Authority(
            level=AUTHORITY_UNKNOWN,
            composite_score=composite,
            component_scores=component_scores,
            reason=reason,
            authority_score=None,
            assessments=tuple(assessments),
            constraints=constraints,
        )

    ceiling = min((c.cap_score for c in constraints), default=1.0)
    authority_score = min(composite, ceiling)

    level = level_for(authority_score, previous_level, policy)
    # The safety invariant: published authority never exceeds the live ceiling.
    #
    # Hysteresis holds a level against a falling score, which is right for
    # noise and wrong for a prerequisite that has actually stopped holding.
    # With a ceiling of 0.82 and a previous level of FULL_AUTONOMOUS, the fall
    # branch alone would keep publishing FULL — 0.82 is inside the 0.80-0.85
    # band — while the cap says ASSISTED. `_bare_level_for` has no memory, so
    # taking the stricter of the two makes a new restriction take effect on the
    # tick it appears. Hysteresis still governs the climb back.
    level = min(level, _bare_level_for(ceiling, policy))

    return Authority(
        level=level,
        composite_score=composite,
        component_scores=component_scores,
        reason=reason,
        authority_score=authority_score,
        assessments=tuple(assessments),
        constraints=constraints,
    )


def evaluate_constraints(sources, essential, policy: Policy = None) -> list[Constraint]:
    """Turn the configured essential requirements into active ceilings.

    `essential` is a set of `(source, subject_or_None)` pairs. A pair with
    `subject=None` is the whole-source shorthand, for a source that genuinely
    is one indivisible requirement; naming a subject is the precise form, and
    the reason it exists is that a GNSS source's position fix can be a hard
    prerequisite while its four ancillary diagnostics are not. Capping on the
    whole source there would let an unread diagnostic veto the vessel.

    A source-level requirement caps at the source's *effective* score, so
    partial coverage of something essential restricts authority — not knowing
    whether a prerequisite holds is not the same as it holding. A subject-level
    requirement caps at that subject's own score, since coverage of the rest of
    the source says nothing about it.

    A requirement the monitor cannot see at all — never advertised, or absent
    from the config entirely — does not cap, it **invalidates**. Otherwise
    deleting a watch, or fat-fingering its name, would be a way to raise the
    vessel's authority.
    """
    if not essential:
        return []

    by_name = {s.name: s for s in sources}
    out: list[Constraint] = []

    for component, subject in sorted(essential, key=lambda r: (r[0], r[1] or "")):
        source = by_name.get(component)
        if source is None:
            out.append(
                Constraint(
                    component,
                    subject,
                    CAUSE_CONFIGURATION_INVALID,
                    0.0,
                    invalidates=True,
                )
            )
            continue

        if subject is None:
            level = source.level
            cap = _assess(source, policy).effective_score
        else:
            state = next(
                (q for q in getattr(source, "subjects", []) or [] if q.name == subject),
                None,
            )
            if state is None:
                out.append(
                    Constraint(
                        component,
                        subject,
                        CAUSE_CONFIGURATION_INVALID,
                        0.0,
                        invalidates=True,
                    )
                )
                continue
            level = state.level
            cap = score_for(level, policy)

        if level == HEALTH_NOT_ADVERTISED:
            out.append(
                Constraint(
                    component, subject, CAUSE_NOT_ADVERTISED, 0.0, invalidates=True
                )
            )
        elif cap < 1.0:
            out.append(
                Constraint(
                    component,
                    subject,
                    _CAUSE_BY_LEVEL.get(level, CAUSE_UNSPECIFIED),
                    cap,
                )
            )

    return out


def _assess(source, policy: Policy = None) -> SourceAssessment:
    """Score one source, discounted by how much of it could be assessed."""
    subjects = getattr(source, "subjects", None) or []
    health = score_for(source.level, policy)

    if not is_scored(source.level):
        # The roll-up itself is a config error. Already excluded; coverage of
        # a source that claims nothing is not a meaningful number.
        return SourceAssessment(source.name, source.level, health, 0.0, 0.0, False)

    coverage = coverage_for(subjects) if subjects else 1.0
    if coverage is None:
        # Unreachable from evaluate_grouped() — a source whose every subject is
        # NOT_ADVERTISED rolls up to NOT_ADVERTISED and is excluded above. Kept
        # so a caller synthesising SourceStates cannot get full coverage for
        # free out of an all-config-error source.
        return SourceAssessment(source.name, source.level, health, 0.0, 0.0, False)

    return SourceAssessment(
        name=source.name,
        level=source.level,
        health_score=health,
        coverage_fraction=coverage,
        effective_score=health * coverage,
        participates=True,
    )


def _build_reason(scored, unscored, constraints=()) -> str:
    """Name the components dragging the score down, worst first.

    The operator needs to know *why* authority dropped, not only that it did —
    "gnss_main not reporting; battery degraded" sends someone to the right box.

    Partial coverage gets its own clause, because it is the one cause the
    health phrases cannot express: a source reported NOMINAL on the one subject
    it answered and said nothing about four others. Naming it "nominal" would
    be true and useless; the score already counts it against the vessel, and
    the prose has to agree with the score about why.

    Sources excluded from the composite are reported too. They did not move the
    score, but they are the one place a watch-config typo is visible at all,
    and dropping them from the prose as well as the arithmetic would make a
    misconfigured monitor look like a healthy one.
    """
    failing = [a for a in scored if a.health_score < 1.0]
    # Fully healthy on everything it answered, but it did not answer everything.
    partial = [a for a in scored if a.health_score == 1.0 and a.coverage_fraction < 1.0]

    if not failing:
        parts = [f"all {len(scored)} components nominal"] if scored else []
    else:
        # Worst first: a critical component matters more than a degraded one.
        failing.sort(key=lambda a: (a.effective_score, a.name))
        named = failing[:_MAX_NAMED]
        parts = [f"{a.name} {_LEVEL_PHRASE.get(a.level, 'unhealthy')}" for a in named]
        remainder = len(failing) - len(named)
        if remainder > 0:
            parts.append(f"and {remainder} more")

    if partial:
        parts.append(_partial_clause(partial))

    if unscored:
        parts.append(f"{_name_list(unscored)} not advertised (excluded)")

    if constraints:
        # Leads, because a cap is the only thing here that is not a matter of
        # degree: it is why the vessel may not do something, not why its score
        # moved.
        parts.insert(0, _constraint_clause(constraints))

    return "; ".join(parts)


_CAUSE_PHRASE = {
    CAUSE_DEGRADED: "degraded",
    CAUSE_CRITICAL: "critical",
    CAUSE_INACTIVE: "inactive",
    CAUSE_UNKNOWN: "not reporting",
    CAUSE_NOT_ADVERTISED: "not advertised",
    CAUSE_CONFIGURATION_INVALID: "missing from config",
}


def _constraint_clause(constraints) -> str:
    """Say which prerequisite is capping the vessel, and why."""
    invalid = [c for c in constraints if c.invalidates]
    shown = invalid or sorted(constraints, key=lambda c: (c.cap_score, c.component))
    named = shown[:_MAX_NAMED]
    parts = [f"{c.component}/{c.subject}" if c.subject else c.component for c in named]
    detail = ", ".join(
        f"{name} {_CAUSE_PHRASE.get(c.cause, 'unavailable')}"
        for name, c in zip(parts, named)
    )
    extra = len(shown) - len(named)
    if extra > 0:
        detail += f", and {extra} more"
    lead = "cannot determine authority" if invalid else "authority capped"
    return f"{lead} ({detail})"


def _name_list(assessments) -> str:
    """`a, b, c` — truncated with a count once past `_MAX_NAMED`."""
    names = ", ".join(sorted(a.name for a in assessments)[:_MAX_NAMED])
    extra = len(assessments) - _MAX_NAMED
    if extra > 0:
        names += f", and {extra} more"
    return names


def _partial_clause(partial) -> str:
    """Name the sources that answered for only part of what they were asked."""
    partial = sorted(partial, key=lambda a: (a.coverage_fraction, a.name))
    named = partial[:_MAX_NAMED]
    shown = ", ".join(f"{a.name} {a.coverage_fraction:.0%}" for a in named)
    extra = len(partial) - len(named)
    if extra > 0:
        shown += f", and {extra} more"
    return f"partially assessed ({shown})"
