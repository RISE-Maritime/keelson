"""Rate + content-rule evaluation for the entity_health connector.

Pure logic with no Zenoh dependency so it can be unit-tested in isolation.
Time is injected (monotonic seconds) — callers pass `now` explicitly.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable

# HealthLevel enum values mirror keelson.EntityHealth_pb2.HealthLevel.
# Duplicated here to keep the evaluator importable without the generated
# protobuf module (useful for fast unit tests).
HEALTH_UNKNOWN = 0
HEALTH_INACTIVE = 1
HEALTH_CRITICAL = 2
HEALTH_DEGRADED = 3
HEALTH_NOMINAL = 4

# Worst → best ranking. Lower rank wins when combining levels.
_RANK = {
    HEALTH_INACTIVE: 0,
    HEALTH_CRITICAL: 1,
    HEALTH_DEGRADED: 2,
    HEALTH_NOMINAL: 3,
    HEALTH_UNKNOWN: 4,
}

_LEVEL_BY_NAME = {
    "UNKNOWN": HEALTH_UNKNOWN,
    "INACTIVE": HEALTH_INACTIVE,
    "CRITICAL": HEALTH_CRITICAL,
    "DEGRADED": HEALTH_DEGRADED,
    "NOMINAL": HEALTH_NOMINAL,
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
    """Declarative definition of what a subsystem should look like on the bus."""

    name: str
    key_expr: str
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
class SubsystemState:
    """Current per-subsystem status summary."""

    name: str
    level: int
    detail: str


class Evaluator:
    """Per-expectation state: sample timestamps + latest payload.

    `record(now, payload)` is called from the subscriber callback.
    `evaluate(now)` produces a `SubsystemState` for publishing.
    """

    def __init__(self, expectation: Expectation, window_s: float | None = None):
        self.expectation = expectation
        # Rate window: explicit override > expectation.window_s.
        self.window_s = window_s if window_s is not None else expectation.window_s
        self._samples: Deque[float] = deque()
        self._last_payload: Any = None
        self._last_sample_at: float | None = None
        # Set of key-expressions for which a liveliness token is currently
        # present. The expectation is considered "alive" iff this is non-empty.
        self.alive_sources: set[str] = set()

    def set_alive(self, key: str) -> None:
        self.alive_sources.add(key)

    def set_dead(self, key: str) -> None:
        self.alive_sources.discard(key)

    @property
    def is_alive(self) -> bool:
        return bool(self.alive_sources)

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

    def evaluate(self, now: float) -> SubsystemState:
        exp = self.expectation

        # Liveliness gate: if required and no token is present → UNKNOWN.
        if exp.require_liveliness and not self.is_alive:
            return SubsystemState(
                exp.name, HEALTH_UNKNOWN, "no liveliness token present"
            )

        # Alive (or liveliness not required) but no samples yet → INACTIVE.
        if self._last_sample_at is None:
            detail = (
                "alive but no samples received yet"
                if exp.require_liveliness
                else "no samples received yet"
            )
            return SubsystemState(exp.name, HEALTH_INACTIVE, detail)

        silence = now - self._last_sample_at
        if silence > exp.inactive_after_s:
            return SubsystemState(
                exp.name,
                HEALTH_INACTIVE,
                f"silent for {silence:.1f}s (limit {exp.inactive_after_s}s)",
            )

        # Collect (level, detail) from rate check + every content rule
        results: list[tuple[int, str]] = [self._publication_rate_level(now)]
        for rule in exp.content_rules:
            results.append(rule.evaluate(self._last_payload))

        overall = worst(*(lv for lv, _ in results))
        if overall == HEALTH_NOMINAL or overall == HEALTH_UNKNOWN:
            return SubsystemState(exp.name, overall or HEALTH_NOMINAL, "ok")

        # Build a detail string from all non-nominal contributors
        details = [d for lv, d in results if lv != HEALTH_NOMINAL and d]
        return SubsystemState(exp.name, overall, "; ".join(details) or "degraded")


def evaluate_all(
    evaluators: Iterable[Evaluator], now: float
) -> tuple[int, list[SubsystemState]]:
    """Aggregate all subsystems into an overall health level.

    Overall level = worst (lowest-rank) of any non-UNKNOWN subsystem,
    or UNKNOWN if the list is empty / all unknown.
    """
    states = [ev.evaluate(now) for ev in evaluators]
    overall = worst(*(s.level for s in states))
    return overall, states
