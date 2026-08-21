"""HealthLevel vocabulary, read from the generated protobuf enum.

`entity_health`'s evaluator mirrors these as plain ints so it stays importable
without the generated module, which is worth it for a package whose unit tests
are pure arithmetic. This connector consumes `EntityHealth` messages and cannot
run without the generated code at all, so it takes the enum as authoritative
rather than keeping a second copy that could drift from it.
"""

from keelson.payloads.EntityHealth_pb2 import HealthLevel

HEALTH_UNKNOWN = HealthLevel.HEALTH_UNKNOWN
HEALTH_INACTIVE = HealthLevel.HEALTH_INACTIVE
HEALTH_CRITICAL = HealthLevel.HEALTH_CRITICAL
HEALTH_DEGRADED = HealthLevel.HEALTH_DEGRADED
HEALTH_NOMINAL = HealthLevel.HEALTH_NOMINAL
HEALTH_NOT_ADVERTISED = HealthLevel.HEALTH_NOT_ADVERTISED


def parse_level(level: int | str) -> int:
    """Accept either an int (HEALTH_*) or a name ("NOMINAL", "HEALTH_NOMINAL")."""
    if isinstance(level, int):
        if level not in HealthLevel.values():
            raise ValueError(f"unknown health level int: {level}")
        return level
    name = level.upper()
    if not name.startswith("HEALTH_"):
        name = f"HEALTH_{name}"
    try:
        return HealthLevel.Value(name)
    except ValueError:
        raise ValueError(f"unknown health level: {level}") from None


# Rollup ordering, mirroring the evaluator that produces the levels this
# connector consumes. Not used at runtime — the wire already carries rolled-up
# source levels — but tests build fixtures the way the wire delivers them, and
# a fixture rolled up by hand would drift from what a producer actually sends.
_RANK = {
    HEALTH_NOT_ADVERTISED: 0,
    HEALTH_INACTIVE: 1,
    HEALTH_CRITICAL: 2,
    HEALTH_DEGRADED: 3,
    HEALTH_NOMINAL: 4,
    HEALTH_UNKNOWN: 5,
}


def worst(*levels: int) -> int:
    """Return the worst (lowest-rank) of the given levels for rollups.

    Two levels are *diagnostic* rather than fault levels and are excluded
    from the aggregate so they can never mask a real fault on a sibling
    subject:

    - ``UNKNOWN`` (no information) — ignored unless nothing else exists.
    - ``NOT_ADVERTISED`` (watch config error: source is up but does not
      claim this subject) — visible per-subject and warned about at
      startup, but a stale watch or typo must not pin the source/entity
      aggregate below a genuine CRITICAL for however long the config
      stays wrong. Preferred over UNKNOWN when only diagnostics exist,
      being the more informative (resolved) of the two.
    """
    faults = [lv for lv in levels if lv not in (HEALTH_UNKNOWN, HEALTH_NOT_ADVERTISED)]
    if faults:
        return min(faults, key=lambda lv: _RANK.get(lv, 99))
    if HEALTH_NOT_ADVERTISED in levels:
        return HEALTH_NOT_ADVERTISED
    return HEALTH_UNKNOWN
