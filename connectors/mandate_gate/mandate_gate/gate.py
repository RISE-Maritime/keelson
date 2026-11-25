"""The mandate gate's decision: hold her short of waters her watch has no mandate for.

Pure. `Gate.update` is called with her latest fix and her watch, and answers
what `mandate_gate` should say. It keeps the one piece of state a hold needs —
where it is, which area it is for, and its id — so the binary is only wiring.

How a hold is placed. A loiter is carried out by making for the point at the
speed she has and lying neutral once near it, so a ship that cannot stop in her
own length runs on past the point by her stopping distance. The point is put
that far short of the line plus the margin, and further back still until the
whole circle she may drift about in keeps the margin from water her watch does
not hold. The hold is asked for once she is within

    stopping distance + margin + radius + speed × reaction time

of the line, measured along her course over the ground. Further out the gate
reads CLEAR and says what lies ahead, so an operator sees the line coming.

What ends a hold:

- **the watch reaching a site that holds the area** — CLEAR, and she stays where
  she is: CLEAR re-arms nothing;
- **her being taken elsewhere** — out of the hold by more than the margin, and
  not on a course into water her watch does not hold. CLEAR;
- **her being taken onward** — out of the hold and on a course into that water
  again. That is a NEW hold, with a new id: the gate stands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mandate_gate.geometry import Area, Projected
from mandate_gate.watch import Watch

KNOT_MPS = 1852.0 / 3600.0

CLEAR = "clear"
HOLDING = "holding"
NO_FIX = "no_fix"

#: Below this over the ground her course is noise, and nothing is projected
#: along it: only whether she is already in forbidden water is judged.
DEFAULT_MIN_COURSE_KNOTS = 0.5

#: Stepping a hold centre back from the line, metres.
_BACK_OFF_STEP_M = 25.0


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    initial_watch: str
    areas: tuple[Area, ...]
    margin_m: float = 200.0
    loiter_radius_m: float = 400.0
    reaction_s: float = 10.0
    #: Her stopping distance with the engine stopped, at `stop_speed_knots`;
    #: scaled in proportion to her speed. 0 for a hull that stops in her length.
    stop_distance_m: float = 0.0
    stop_speed_knots: float = 10.0
    #: How far ahead, in time at her speed, an area is reported before it holds her.
    warn_s: float = 600.0
    fix_max_age_s: float = 10.0
    min_course_knots: float = DEFAULT_MIN_COURSE_KNOTS


def _number(doc: dict, key: str, default: float, *, positive: bool = False) -> float:
    value = doc.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ConfigError(f"{key} must be a number, not {value!r}")
    if value < 0 or (positive and value == 0):
        raise ConfigError(
            f"{key} must be {'more than' if positive else 'at least'} 0, not {value}"
        )
    return float(value)


def load_config(doc: dict) -> Config:
    """A Config from its JSON form, or ConfigError saying what is wrong."""
    if not isinstance(doc, dict):
        raise ConfigError("the config is a JSON object")
    known = {
        "initial_watch",
        "areas",
        "margin_m",
        "loiter_radius_m",
        "reaction_s",
        "stopping",
        "warn_s",
        "fix_max_age_s",
        "min_course_knots",
    }
    extra = set(doc) - known
    if extra:
        raise ConfigError(f"unknown keys {sorted(extra)}")
    watch = doc.get("initial_watch")
    if not isinstance(watch, str) or not watch.strip():
        raise ConfigError("initial_watch names the ROC site on her watch at the start")
    raw_areas = doc.get("areas")
    if not isinstance(raw_areas, list) or not raw_areas:
        raise ConfigError("areas lists at least one mandate area")
    areas, seen = [], set()
    for i, raw in enumerate(raw_areas):
        where = f"areas[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} is an object")
        area_id = raw.get("id")
        if not isinstance(area_id, str) or not area_id:
            raise ConfigError(f"{where} needs an id")
        if area_id in seen:
            raise ConfigError(f"{where}: id {area_id!r} is used twice")
        seen.add(area_id)
        sites = raw.get("roc_sites")
        if (
            not isinstance(sites, list)
            or not sites
            or not all(isinstance(s, str) and s.strip() for s in sites)
        ):
            raise ConfigError(
                f"{where} ({area_id}): roc_sites names the sites holding its mandate"
            )
        ring = raw.get("polygon")
        if not isinstance(ring, list) or len(ring) < 3:
            raise ConfigError(
                f"{where} ({area_id}): polygon is at least three [lat, lon] points"
            )
        points = []
        for point in ring:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or not all(
                    isinstance(c, (int, float)) and not isinstance(c, bool)
                    for c in point
                )
                or not (-90 <= point[0] <= 90 and -180 <= point[1] <= 180)
            ):
                raise ConfigError(
                    f"{where} ({area_id}): {point!r} is not a [lat, lon] point"
                )
            points.append((float(point[0]), float(point[1])))
        if points[0] == points[-1]:
            points.pop()
        areas.append(Area(area_id, frozenset(s.strip() for s in sites), tuple(points)))
    stopping = doc.get("stopping", {})
    if not isinstance(stopping, dict):
        raise ConfigError("stopping is {speed_knots, distance_m}")
    return Config(
        initial_watch=watch.strip(),
        areas=tuple(areas),
        margin_m=_number(doc, "margin_m", 200.0),
        loiter_radius_m=_number(doc, "loiter_radius_m", 400.0, positive=True),
        reaction_s=_number(doc, "reaction_s", 10.0),
        stop_distance_m=_number(stopping, "distance_m", 0.0),
        stop_speed_knots=_number(stopping, "speed_knots", 10.0, positive=True),
        warn_s=_number(doc, "warn_s", 600.0),
        fix_max_age_s=_number(doc, "fix_max_age_s", 10.0, positive=True),
        min_course_knots=_number(doc, "min_course_knots", DEFAULT_MIN_COURSE_KNOTS),
    )


@dataclass(frozen=True)
class Fix:
    """Her own reading of where she is and how she is going. Course and speed may be absent."""

    lat: float
    lon: float
    cog_deg: float | None
    sog_knots: float | None


@dataclass(frozen=True)
class Hold:
    lat: float
    lon: float
    radius_m: float


@dataclass(frozen=True)
class Report:
    """What `mandate_gate` carries, short of its timestamp."""

    state: str
    reason: str
    watch: Watch
    zone_ahead: str = ""
    distance_to_boundary_m: float | None = None
    hold: Hold | None = None
    hold_id: int = 0


def _km(metres: float) -> str:
    return f"{metres / 1000.0:.1f} km" if metres >= 1000 else f"{metres:.0f} m"


@dataclass
class Gate:
    config: Config
    holding: bool = False
    hold: Hold | None = None
    hold_area: Area | None = None
    hold_id: int = 0

    def stopping_m(self, sog_mps: float) -> float:
        c = self.config
        return c.stop_distance_m * sog_mps / (c.stop_speed_knots * KNOT_MPS)

    def update(self, fix: Fix | None, watch: Watch) -> Report:
        c = self.config
        if fix is None:
            return self._report(
                NO_FIX,
                "no fresh position: the gate cannot judge, and keeps any hold it has asked for",
                watch,
            )
        projected = Projected.of(list(c.areas), fix.lat, fix.lon)
        here = (0.0, 0.0)
        sog_kn = fix.sog_knots if fix.sog_knots is not None else 0.0
        moving = fix.cog_deg is not None and sog_kn >= c.min_course_knots
        v = sog_kn * KNOT_MPS if moving else 0.0
        stop = self.stopping_m(v)
        trigger = stop + c.margin_m + c.loiter_radius_m + v * c.reaction_s
        if moving:
            entry = projected.first_entry(
                here, fix.cog_deg, watch.site, trigger + v * c.warn_s
            )
        else:
            area = projected.forbidding(here, watch.site)
            entry = (0.0, area) if area is not None else None
        due = entry is not None and entry[0] <= trigger

        if self.holding:
            if watch.site in self.hold_area.roc_sites:
                area_id = self.hold_area.area_id
                self._release()
                return self._report(
                    CLEAR,
                    f"the watch is with {watch.site}, which holds the mandate for {area_id}; "
                    "she stays in her hold until she is ordered on",
                    watch,
                    entry,
                )
            centre = projected.plane.to_en(self.hold.lat, self.hold.lon)
            outside = math.hypot(*centre) > self.hold.radius_m + c.margin_m
            if outside and due:
                return self._new_hold(
                    projected,
                    fix,
                    watch,
                    entry,
                    stop,
                    reason_prefix="taken out of her hold toward ",
                )
            if outside:
                self._release()
                return self._report(
                    CLEAR,
                    "she has been taken out of her hold, away from water "
                    f"{watch.site} holds no mandate for",
                    watch,
                    entry,
                )
            return self._report(
                HOLDING,
                f"holding: {watch.site} holds no mandate in {self.hold_area.area_id}; "
                "hand the watch over",
                watch,
                entry if entry is not None and entry[1] is self.hold_area else None,
            )

        if due:
            return self._new_hold(projected, fix, watch, entry, stop)
        if entry is not None:
            return self._report(
                CLEAR,
                f"{entry[1].area_id} is {_km(entry[0])} ahead and {watch.site} holds no mandate "
                "there: hand the watch over before she reaches it, or she will be held short",
                watch,
                entry,
            )
        return self._report(
            CLEAR, f"{watch.site} holds the mandate for the water ahead", watch
        )

    # --- internals -----------------------------------------------------------------

    def _new_hold(
        self,
        projected: Projected,
        fix: Fix,
        watch: Watch,
        entry,
        stop: float,
        reason_prefix: str = "",
    ):
        c = self.config
        distance, area = entry
        late = False
        if distance <= 0.0:
            centre = (0.0, 0.0)
            why = f"already in {area.area_id}, where {watch.site} holds no mandate"
        else:
            s = distance - c.margin_m - stop
            need = c.loiter_radius_m + c.margin_m
            while (
                s > 0.0
                and projected.clearance(
                    projected.along((0.0, 0.0), fix.cog_deg, s), watch.site
                )
                < need
            ):
                s -= _BACK_OFF_STEP_M
            if s <= 0.0:
                s, late = 0.0, True
            centre = projected.along((0.0, 0.0), fix.cog_deg, s)
            why = f"{area.area_id} is {_km(distance)} ahead and {watch.site} holds no mandate there"
        lat, lon = projected.plane.to_latlon(*centre)
        self.holding = True
        self.hold = Hold(lat, lon, c.loiter_radius_m)
        self.hold_area = area
        self.hold_id += 1
        reason = f"holding: {reason_prefix}{why}; hand the watch over"
        if late:
            reason += " (too close to stop short at her speed: held where she is)"
        return self._report(HOLDING, reason, watch, entry)

    def _release(self) -> None:
        self.holding = False
        self.hold = None
        self.hold_area = None

    def _report(self, state: str, reason: str, watch: Watch, entry=None) -> Report:
        return Report(
            state=state,
            reason=reason,
            watch=watch,
            zone_ahead=(
                entry[1].area_id
                if entry is not None
                else (self.hold_area.area_id if self.holding else "")
            ),
            distance_to_boundary_m=entry[0] if entry is not None else None,
            hold=self.hold if self.holding else None,
            hold_id=self.hold_id,
        )
