"""ISO 22133 object states and the local→geodetic conversion MONR needs.

State values are the standard's own numbering, read from RI-SE/iso22133
`include/defines.h` (enum ObjectStateValues). They are not renumbered anywhere
in this connector or in keelson's TestObjectStatus.proto: a conformance record
is only worth keeping if the state it names is the state the object reported.
"""

from __future__ import annotations

import math

# enum ObjectStateValues — include/defines.h
OFF = 0
INIT = 1
ARMED = 2
DISARMED = 3
RUNNING = 4
POSTRUN = 5
REMOTE_CONTROLLED = 6
ABORTING = 7
PRE_ARMING = 8
PRE_RUNNING = 9
UNAVAILABLE = 255

STATE_NAMES = {
    OFF: "OFF",
    INIT: "INIT",
    ARMED: "ARMED",
    DISARMED: "DISARMED",
    RUNNING: "RUNNING",
    POSTRUN: "POSTRUN",
    REMOTE_CONTROLLED: "REMOTE_CONTROLLED",
    ABORTING: "ABORTING",
    PRE_ARMING: "PRE_ARMING",
    PRE_RUNNING: "PRE_RUNNING",
    UNAVAILABLE: "UNAVAILABLE",
}

# enum ArmReadinessValues
NOT_READY_TO_ARM = 0
READY_TO_ARM = 1
READY_TO_ARM_UNAVAILABLE = 255

# enum ControlCenterStatusType
CC_INIT = 0
CC_READY = 1
CC_ABORT = 2
CC_RUNNING = 3
CC_TEST_DONE = 4
CC_NORMAL_STOP = 5

#: Transitions the object may make, for MONITORING only.
#:
#: This connector never commands a transition — it has no OSTM or STRT path at
#: all. The table exists so an observer can flag a jump that the standard does
#: not allow, which is a genuine finding about the object under test and one an
#: operator would otherwise have to spot by eye in a log.
#:
#: ABORTING is reachable from everywhere on purpose: an abort that some state
#: could refuse would not be an abort.
LEGAL_TRANSITIONS: dict[int, set[int]] = {
    OFF: {INIT},
    INIT: {DISARMED, ABORTING},
    DISARMED: {PRE_ARMING, REMOTE_CONTROLLED, ABORTING, OFF},
    PRE_ARMING: {ARMED, DISARMED, ABORTING},
    ARMED: {PRE_RUNNING, DISARMED, ABORTING},
    PRE_RUNNING: {RUNNING, ARMED, ABORTING},
    RUNNING: {POSTRUN, ABORTING},
    POSTRUN: {DISARMED, ABORTING, OFF},
    REMOTE_CONTROLLED: {DISARMED, ABORTING},
    ABORTING: {DISARMED, OFF},
    # UNAVAILABLE is deliberately absent. It is not a state the machine moves
    # through — it is the object declining to say where it is — so it belongs
    # on neither side of a transition check. Listing it with an empty set of
    # exits made every recovery from it read as a violation.
}


def state_name(state: int) -> str:
    return STATE_NAMES.get(state, f"UNKNOWN({state})")


def is_legal_transition(previous: int, current: int) -> bool:
    """Was this a transition the standard permits?

    Staying put is always legal — MONR streams at rate, so the overwhelming
    majority of samples repeat the previous state. An unknown previous state is
    treated as legal rather than reported: the first sample after a connector
    restart has nothing to compare against, and calling that a violation would
    put a fault on the record every time the bridge is bounced.

    UNAVAILABLE on either side is likewise not a violation. It means the object
    is not reporting its state, so there is no transition to judge — flagging
    one would be reporting a fault about the reporting, not about the object.
    """
    if previous == current:
        return True
    if previous == UNAVAILABLE or current == UNAVAILABLE:
        return True
    if previous not in LEGAL_TRANSITIONS:
        return True
    return current in LEGAL_TRANSITIONS[previous]


# --- local ENU -> WGS84 -----------------------------------------------------
#
# MONR carries x/y/z in millimetres relative to the test origin, NOT latitude
# and longitude: the origin is configured on the object via OSEM. To publish a
# `location_fix` the connector therefore has to be told where the origin is —
# there is nothing in MONR to infer it from, and guessing would put the object
# somewhere off West Africa.

EARTH_RADIUS_M = 6378137.0


def enu_to_wgs84(
    origin_lat: float, origin_lon: float, x_east_m: float, y_north_m: float
):
    """Flat-earth ENU offset to latitude/longitude.

    Flat-earth is appropriate here and not a shortcut: ISO 22133 test areas are
    proving grounds, hundreds of metres across, where the error against a proper
    geodetic solution is well under the positioning accuracy the standard itself
    reports on. It would be the wrong choice for anything basin-scale.
    """
    lat = origin_lat + math.degrees(y_north_m / EARTH_RADIUS_M)
    lon = origin_lon + math.degrees(
        x_east_m / (EARTH_RADIUS_M * math.cos(math.radians(origin_lat)))
    )
    return lat, lon


def speed_to_knots(mps: float | None) -> float | None:
    return None if mps is None else mps * 1.9438444924406


def heading_from_yaw(yaw_deg: float | None) -> float | None:
    """MONR yaw to a 0-360 true heading.

    NOTE, and this is a real caveat rather than a formality: ISO 22133 yaw is
    measured in the test-area's local frame, whose alignment to true north is a
    property of the site, not of the message. This returns the yaw normalised
    into 0-360 and the connector exposes `--yaw-offset-deg` for the site
    rotation. Publishing raw yaw as `heading_true_north_deg` without that offset
    would be asserting a bearing nobody measured.
    """
    if yaw_deg is None:
        return None
    return yaw_deg % 360.0
