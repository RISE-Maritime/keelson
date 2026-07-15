"""Rate + content-rule evaluation for the entity_health connector.

Pure logic with no Zenoh dependency so it can be unit-tested in isolation.
Time is injected (monotonic seconds) — callers pass `now` explicitly.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Mapping

# HealthLevel enum values mirror keelson.EntityHealth_pb2.HealthLevel.
# Duplicated here to keep the evaluator importable without the generated
# protobuf module (useful for fast unit tests).
HEALTH_UNKNOWN = 0
HEALTH_INACTIVE = 1
HEALTH_CRITICAL = 2
HEALTH_DEGRADED = 3
HEALTH_NOMINAL = 4
# The source's source-level liveliness token is present, but it does not
# advertise a subject-level token for this subject — a resolved negative
# (typically an operator config error: subject-name typo, wrong
# source_id), distinct from UNKNOWN (no information) and INACTIVE
# (advertised but silent). Only raised against sources that advertise at
# least one subject-level token (three-tier adopters).
HEALTH_NOT_ADVERTISED = 5

# Worst → best ranking. Lower rank wins when combining levels. NOT_ADVERTISED
# is the worst non-UNKNOWN outcome: it's a resolved negative (the operator
# pointed the config at a subject the source never advertises), which is
# more actionable/worse than merely INACTIVE (advertised but silent).
_RANK = {
    HEALTH_NOT_ADVERTISED: 0,
    HEALTH_INACTIVE: 1,
    HEALTH_CRITICAL: 2,
    HEALTH_DEGRADED: 3,
    HEALTH_NOMINAL: 4,
    HEALTH_UNKNOWN: 5,
}

_LEVEL_BY_NAME = {
    "UNKNOWN": HEALTH_UNKNOWN,
    "INACTIVE": HEALTH_INACTIVE,
    "CRITICAL": HEALTH_CRITICAL,
    "DEGRADED": HEALTH_DEGRADED,
    "NOMINAL": HEALTH_NOMINAL,
    "NOT_ADVERTISED": HEALTH_NOT_ADVERTISED,
}

_NAME_BY_LEVEL = {v: k for k, v in _LEVEL_BY_NAME.items()}


def parse_level(level: int | str) -> int:
    """Accept either an int (HEALTH_*) or a string ("NOMINAL", "HEALTH_NOMINAL")."""
    if isinstance(level, int):
        if level not in _NAME_BY_LEVEL:
            raise ValueError(f"unknown health level int: {level}")
        return level
    name = level.upper().removeprefix("HEALTH_")
    if name not in _LEVEL_BY_NAME:
        raise ValueError(f"unknown health level: {level}")
    return _LEVEL_BY_NAME[name]


def worst(*levels: int) -> int:
    """Return the worst (lowest-rank) of the given levels, ignoring UNKNOWN."""
    non_unknown = [lv for lv in levels if lv != HEALTH_UNKNOWN]
    if not non_unknown:
        return HEALTH_UNKNOWN
    return min(non_unknown, key=lambda lv: _RANK.get(lv, 99))


@dataclass
class Band:
    """A value predicate that maps to a specific health level.

    Supports either a numeric range (`min`/`max`) or an equality / set
    match (`equals` — scalar or list of scalars). If `equals` is set it
    takes precedence over `min`/`max`.
    """

    level: int
    min: float | None = None
    max: float | None = None
    equals: Any = None

    def contains(self, value: Any) -> bool:
        if self.equals is not None:
            if isinstance(self.equals, (list, tuple, set)):
                return value in self.equals
            return value == self.equals
        try:
            if self.min is not None and value < self.min:
                return False
            if self.max is not None and value > self.max:
                return False
        except TypeError:
            # Non-numeric value with a numeric band → no match
            return False
        return True


@dataclass
class ContentRule:
    """Tiered range/equality check on a top-level proto field.

    Bands are evaluated best→worst (NOMINAL first). The first band whose
    predicate matches the field value wins. If no band matches, the
    rule produces `default_level` (CRITICAL by default).
    """

    field: str
    bands: list[Band] = field(default_factory=list)
    default_level: int = HEALTH_CRITICAL

    def __post_init__(self) -> None:
        # Sort bands best→worst so we always return the most favourable match
        self.bands.sort(key=lambda b: _RANK.get(b.level, 99), reverse=True)

    def evaluate(self, payload: Any) -> tuple[int, str]:
        """Return (level, detail). UNKNOWN means rule could not be applied."""
        if payload is None:
            return HEALTH_UNKNOWN, ""
        try:
            value = getattr(payload, self.field)
        except AttributeError:
            return HEALTH_DEGRADED, f"missing field {self.field}"

        # If the field is a protobuf enum, also compute the symbolic name so
        # that bands can match on either the int or the enum name string.
        name: str | None = None
        try:
            desc = type(payload).DESCRIPTOR.fields_by_name.get(self.field)
            if desc is not None and desc.enum_type is not None:
                enum_value = desc.enum_type.values_by_number.get(value)
                if enum_value is not None:
                    name = enum_value.name
        except (AttributeError, TypeError):
            pass

        for band in self.bands:
            if band.contains(value) or (name is not None and band.contains(name)):
                if band.level == HEALTH_NOMINAL:
                    return HEALTH_NOMINAL, ""
                shown = name if name is not None else value
                return (
                    band.level,
                    f"{self.field}={shown} in {_NAME_BY_LEVEL[band.level]} band",
                )
        shown = name if name is not None else value
        return (
            self.default_level,
            f"{self.field}={shown} outside all bands",
        )


@dataclass
class Expectation:
    """Declarative definition of one (source, subject) pair on the bus.

    `name` is the subject name (matches `SubjectHealth.name`). The owning
    source is tracked outside this dataclass — Expectations are looked up
    by `(source_name, subject_name)` keys.
    """

    name: str
    inactive_after_s: float = 10.0
    window_s: float = 10.0
    publication_rate_hz: list[Band] = field(default_factory=list)
    publication_rate_default_level: int = HEALTH_CRITICAL
    content_rules: list[ContentRule] = field(default_factory=list)
    require_liveliness: bool = True

    def __post_init__(self) -> None:
        self.publication_rate_hz.sort(
            key=lambda b: _RANK.get(b.level, 99), reverse=True
        )


@dataclass
class CheckResult:
    """Result of a single check (publication rate or one content rule)."""

    name: str
    level: int
    detail: str = ""


@dataclass
class SubjectState:
    """Current per-subject status summary.

    `name` is the subject name. All structured per-check info lives in
    `checks`. Liveliness failures are conveyed by `level == HEALTH_UNKNOWN`
    with `checks == []`. A source that is present but doesn't advertise
    this subject is conveyed by `level == HEALTH_NOT_ADVERTISED` with a
    single explanatory `checks` entry (see `Evaluator.evaluate`).
    """

    name: str
    level: int
    measured_publication_rate_hz: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)


@dataclass
class SourceState:
    """Aggregated health of one source (device) and its subjects."""

    name: str
    level: int
    subjects: list[SubjectState] = field(default_factory=list)


@dataclass
class SourceLiveliness:
    """Shared liveliness state for one source, referenced by every
    `Evaluator` for that source's subjects.

    Pure data (no Zenoh dependency) so it's directly injectable in unit
    tests. The connector wires it up from Zenoh liveliness samples:

    - `source_tokens` — the set of currently-live key expressions that
      count as *source-level presence evidence*: the new source-level
      token (`{realm}/@v0/{entity}/*/{source}`) and/or the legacy coarse
      token (`{realm}/@v0/{entity}/pubsub/*/{source}`, from connectors
      that haven't adopted three-tier liveliness yet). The source is
      "present" iff this set is non-empty.
    - `advertised_subjects` — the set of subject names for which a live
      subject-level pubsub token (`{realm}/@v0/{entity}/pubsub/{subject}/{source}`)
      currently exists. This conveys *capability*, not activity: it can be
      non-empty even while the subject is silent.
    """

    source_tokens: set[str] = field(default_factory=set)
    advertised_subjects: set[str] = field(default_factory=set)

    def add_source_token(self, key: str) -> None:
        self.source_tokens.add(key)

    def remove_source_token(self, key: str) -> None:
        self.source_tokens.discard(key)

    def add_subject(self, subject: str) -> None:
        self.advertised_subjects.add(subject)

    def remove_subject(self, subject: str) -> None:
        self.advertised_subjects.discard(subject)

    @property
    def is_present(self) -> bool:
        """Whether the source has at least one live presence token."""
        return bool(self.source_tokens)


class Evaluator:
    """Per-expectation state: sample timestamps + latest payload.

    `record(now, payload)` is called from the subscriber callback.
    `evaluate(now)` produces a `SubjectState` for publishing.

    Each Evaluator holds a reference to its source's shared
    `SourceLiveliness` (multiple Evaluators — one per subject — share the
    same instance for a given source). The Evaluator's own subject name
    is `expectation.name`.
    """

    def __init__(
        self,
        expectation: Expectation,
        window_s: float | None = None,
        liveliness: "SourceLiveliness | None" = None,
    ):
        self.expectation = expectation
        # Rate window: explicit override > expectation.window_s.
        self.window_s = window_s if window_s is not None else expectation.window_s
        self._samples: Deque[float] = deque()
        self._last_payload: Any = None
        self._last_sample_at: float | None = None
        # Shared per-source liveliness state. Defaults to a private
        # instance so an Evaluator is usable standalone in tests; the
        # connector always passes the source's shared instance so every
        # subject Evaluator for that source observes the same tokens.
        self.liveliness: SourceLiveliness = (
            liveliness if liveliness is not None else SourceLiveliness()
        )

    def record(self, now: float, payload: Any = None) -> None:
        self._samples.append(now)
        self._last_sample_at = now
        self._last_payload = payload
        self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._samples and self._samples[0] < cutoff:
            self._samples.popleft()

    def observed_rate_hz(self, now: float) -> float:
        self._trim(now)
        if not self._samples:
            return 0.0
        return len(self._samples) / self.window_s

    def _publication_rate_level(self, now: float) -> tuple[int, str]:
        exp = self.expectation
        if not exp.publication_rate_hz:
            return HEALTH_NOMINAL, ""
        observed = self.observed_rate_hz(now)
        for band in exp.publication_rate_hz:
            if band.contains(observed):
                if band.level == HEALTH_NOMINAL:
                    return HEALTH_NOMINAL, ""
                return (
                    band.level,
                    f"rate {observed:.2f}Hz in {_NAME_BY_LEVEL[band.level]} band",
                )
        return (
            exp.publication_rate_default_level,
            f"rate {observed:.2f}Hz outside all rate bands",
        )

    def _activity_check(self, now: float) -> CheckResult:
        """Evaluate the standard `activity` check (samples flowing recently)."""
        exp = self.expectation
        if self._last_sample_at is None:
            detail = (
                "alive but no samples received yet"
                if exp.require_liveliness
                else "no samples received yet"
            )
            return CheckResult("activity", HEALTH_INACTIVE, detail)

        silence = now - self._last_sample_at
        if silence > exp.inactive_after_s:
            return CheckResult(
                "activity",
                HEALTH_INACTIVE,
                f"silent for {silence:.1f}s (limit {exp.inactive_after_s}s)",
            )
        return CheckResult("activity", HEALTH_NOMINAL)

    def _evaluate_active(self, now: float, rate: float) -> SubjectState:
        """Activity gate → rate/content checks, with no liveliness gating.

        Shared by the `require_liveliness=False` bypass path and by the
        `require_liveliness=True` paths where the source is known present
        (subject advertised, or a legacy source that hasn't advertised
        anything at the subject level yet).
        """
        exp = self.expectation

        # Activity check: always runs, and gates rate + content rules. If we
        # haven't seen samples within `inactive_after_s`, only `activity` is
        # emitted — evaluating rate or content rules without samples would
        # be misleading.
        activity = self._activity_check(now)
        if activity.level != HEALTH_NOMINAL:
            return SubjectState(exp.name, activity.level, rate, [activity])

        # Full eval: activity + rate + content rules
        checks: list[CheckResult] = [
            activity,
            CheckResult("publication_rate", *self._publication_rate_level(now)),
        ]
        for rule in exp.content_rules:
            checks.append(CheckResult(rule.field, *rule.evaluate(self._last_payload)))

        overall = worst(*(c.level for c in checks)) or HEALTH_NOMINAL
        return SubjectState(exp.name, overall, rate, checks)

    def evaluate(self, now: float) -> SubjectState:
        """Produce the current `SubjectState`.

        When `require_liveliness` is False, liveliness is not consulted at
        all — behaves exactly like the pre-three-tier evaluator.

        When `require_liveliness` is True, the shared `SourceLiveliness`
        drives a small state machine:

        a. No source presence at all (`source_tokens` empty) → UNKNOWN,
           no checks — nothing else can be evaluated.
        b. Source present, this subject is in `advertised_subjects` → full
           evaluation (activity gate → rate/content checks).
        c. Source present, this subject is *not* advertised, but the
           source *does* advertise at least one other subject → the
           source has adopted three-tier liveliness and simply isn't
           configured to publish this subject: NOT_ADVERTISED, with a
           single explanatory check.
        d. Source present, `advertised_subjects` is empty entirely
           (legacy source: only the coarse token is visible, no
           subject-level tokens at all) → transitional fallback,
           identical to (b) — this is the "not yet three-tier" case
           where we can't distinguish "not configured" from "configured
           but never publishes", so we fall back to activity-based
           detection like before.
        """
        exp = self.expectation
        rate = self.observed_rate_hz(now)

        if not exp.require_liveliness:
            return self._evaluate_active(now, rate)

        live = self.liveliness

        # (a) No source presence at all.
        if not live.is_present:
            return SubjectState(exp.name, HEALTH_UNKNOWN, rate, [])

        # (c) Source is a three-tier adopter (advertises >=1 subject) but
        # not this one.
        if live.advertised_subjects and exp.name not in live.advertised_subjects:
            detail = (
                f"source advertises {len(live.advertised_subjects)} subject(s) "
                "but not this one — check subject name / source_id"
            )
            return SubjectState(
                exp.name,
                HEALTH_NOT_ADVERTISED,
                rate,
                [CheckResult("advertised", HEALTH_NOT_ADVERTISED, detail)],
            )

        # (b) Advertised, or (d) legacy source with no subject-level tokens
        # at all — both fall back to activity-based evaluation.
        return self._evaluate_active(now, rate)


def evaluate_grouped(
    evaluators: Mapping[tuple[str, str], Evaluator], now: float
) -> tuple[int, list[SourceState]]:
    """Group evaluators by source and aggregate.

    Keys are `(source_name, subject_name)`. Returns the entity-wide
    overall level (worst of any non-UNKNOWN source, UNKNOWN otherwise)
    and one `SourceState` per source — each rolling its subjects up via
    `worst()`. Source order is the insertion order of `evaluators`.
    """
    by_source: dict[str, list[SubjectState]] = {}
    for key, ev in evaluators.items():
        source_name = key[0]
        by_source.setdefault(source_name, []).append(ev.evaluate(now))

    sources: list[SourceState] = []
    for source_name, subjects in by_source.items():
        sources.append(
            SourceState(
                name=source_name,
                level=worst(*(s.level for s in subjects)),
                subjects=subjects,
            )
        )

    overall = worst(*(s.level for s in sources))
    return overall, sources
