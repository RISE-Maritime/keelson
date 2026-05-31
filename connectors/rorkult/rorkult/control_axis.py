"""ControlAxisState: subscribers + scaling + dead-man + loopback guard.

Ported from connectors/mavlink with the MAVLink-specific bits stripped:
no RC channel mapping, no PWM, no MAVLink send. The "emit" step is a
no-op extension point until MCU framing is decided; everything else
(scaling, polarity/inversion, min-interval throttle, dead-man, engage/
disengage logging, loopback guard) is real and exercised by the RPC
handlers and unit tests.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import keelson
from keelson.interfaces.VehicleControl_pb2 import (
    ControlAxis,
    ControlAxisMapping,
)
from keelson.payloads.Primitives_pb2 import TimestampedFloat

logger = logging.getLogger("rorkult.control_axis")


# Axis names recognized by v1. Mirrors the mavlink Rover frame
# (steering / throttle). Future axes (roll, pitch, yaw, brake, gear)
# get added here; ``set_mapping`` rejects anything not in this set.
RECOGNISED_AXES: frozenset[str] = frozenset({"steering", "throttle"})


# Default dead-man window applied when ControlAxisMapping.max_axis_age_s
# is 0 (= unset proto3 default). Documented in the proto comment.
# Publishers contractually publish at >=10 Hz; 1.0 s gives ~10x jitter
# headroom.
_DEFAULT_MAX_AXIS_AGE_S: float = 1.0


class LoopbackError(ValueError):
    """A ControlAxis would subscribe to a key the connector itself publishes."""


def _scale_axis_value(raw_pct: float, *, unipolar: bool, invert: bool) -> float:
    """Map a TimestampedFloat value (in percent) to a unitless [-1, 1]
    deflection ready for the MCU command encoder.

    Bipolar (default): raw range [-100, 100] -> [-1.0, 1.0]; raw=0 -> neutral.
    Unipolar (e.g. trigger): raw range [0, 100] -> [0.0, 1.0]; raw=0 -> neutral,
        raw=100 -> full forward. Reverse is unreachable on a unipolar source.
    """
    if unipolar:
        v = max(0.0, min(100.0, raw_pct)) / 100.0
    else:
        v = max(-100.0, min(100.0, raw_pct)) / 100.0
    if invert:
        v = -v
    return v


def _source_id_overlaps(pattern: str, target: str) -> bool:
    """Lightweight check: does the source_id ``pattern`` cover ``target``?

    Catches the obvious cases (`**`, `*`, exact match, `prefix/**`,
    `prefix/*`). Not a full key-expression intersection — Zenoh's own
    subscriber installation would catch a perfect false-negative. Used
    by the loopback guard to reject subscriptions pointing back at the
    connector's own publish surface. Mirrors the helper in
    connectors/mavlink/bin/injection_config.py.
    """
    if pattern == target:
        return True
    if pattern in ("**", "*"):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if target == prefix or target.startswith(prefix + "/"):
            return True
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        if target.startswith(prefix + "/") and "/" not in target[len(prefix) + 1 :]:
            return True
    return False


def _is_loopback(
    *,
    axis_entity_id: str,
    axis_source_id: str,
    connector_entity_id: str,
    connector_source_id: str,
) -> bool:
    """Would a subscription with this (entity_id, source_id) match a key
    this connector publishes?

    The connector publishes under three source_id variants:
      - ``{connector_source_id}`` (entity_health, vehicle_armed)
      - ``{connector_source_id}/setpoint`` (forwarded actuator setpoints)
      - ``{connector_source_id}/measured`` (MCU-measured actuator state)

    Any pattern overlapping any of those (on the connector's own
    entity) is a loopback.
    """
    if axis_entity_id != connector_entity_id:
        return False
    publish_source_ids = (
        connector_source_id,
        f"{connector_source_id}/setpoint",
        f"{connector_source_id}/measured",
    )
    return any(_source_id_overlaps(axis_source_id, s) for s in publish_source_ids)


@dataclass
class _AxisRuntime:
    """Per-axis state held by ControlAxisState. ``last_received_at`` of
    0.0 means "no sample has landed yet" — the dead-man treats that as
    not-ready and blocks emission until every mapped axis has produced
    at least one value."""

    name: str
    config: ControlAxis
    subscriber: Any  # zenoh.Subscriber
    last_value: Optional[float] = None
    last_received_at: float = 0.0


class ControlAxisState:
    """Owns the live per-axis subscriber set + computes the unit values
    that would be forwarded to the MCU on each axis arrival.

    No axes are subscribed at startup. The set is installed by the
    VehicleControl.set_control_mapping RPC; calling it again atomically
    replaces the active set. There is no CLI default — the operator
    must explicitly wire the mapping, so the connector boots
    undrivable by default.

    Emission semantics match the proto contract (see
    interfaces/VehicleControl.proto): strictly sample-driven, one
    forward attempt per arriving Zenoh sample, dead-man fails closed
    when any mapped axis goes silent. The actual MCU emit is currently
    a debug log line — replaced with framing-aware encoding once the
    wire format is decided. Everything before the emit (rate limit,
    dead-man, scaling) is real and exercised.
    """

    def __init__(
        self,
        session: "Any",  # zenoh.Session
        connector_realm: str,
        connector_entity_id: str,
        connector_source_id: str,
    ) -> None:
        self._session = session
        self._realm = connector_realm
        self._connector_entity_id = connector_entity_id
        self._connector_source_id = connector_source_id
        self._axes: dict[str, _AxisRuntime] = {}
        self._min_interval_s: float = 0.0
        self._max_axis_age_s: float = 0.0
        self._last_emit_at: float = 0.0
        self._emitting: bool = False
        self._lock = threading.Lock()

    # ---- public API -----------------------------------------------------

    def set_mapping(self, mapping: ControlAxisMapping) -> None:
        """Replace the active mapping atomically.

        Validates up-front (axis vocabulary + non-empty subject + loopback
        guard) so a bad request is rejected with the old mapping still
        intact. Raises ``ValueError`` (or ``LoopbackError``) on failure;
        the RPC handler turns those into ``reply_err``.
        """
        # Validate everything before mutating any state.
        for axis_name, axis_cfg in mapping.axes.items():
            if axis_name not in RECOGNISED_AXES:
                raise ValueError(
                    f"unknown axis {axis_name!r}; recognised: "
                    f"{sorted(RECOGNISED_AXES)}"
                )
            if not axis_cfg.subject:
                raise ValueError(f"axis {axis_name!r} has empty subject")
            entity_id = axis_cfg.entity_id or self._connector_entity_id
            source_id = axis_cfg.source_id or "**"
            if _is_loopback(
                axis_entity_id=entity_id,
                axis_source_id=source_id,
                connector_entity_id=self._connector_entity_id,
                connector_source_id=self._connector_source_id,
            ):
                raise LoopbackError(
                    f"axis {axis_name!r} would subscribe to source_id "
                    f"{source_id!r} on entity {entity_id!r}, which overlaps "
                    f"this connector's own publish surface "
                    f"(source_id={self._connector_source_id!r}). Pick a more "
                    f"specific source_id pattern."
                )

        # Tear down current state, then install new subscribers.
        with self._lock:
            for axis in self._axes.values():
                try:
                    axis.subscriber.undeclare()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to undeclare axis %s subscriber", axis.name
                    )
            self._axes.clear()
            self._min_interval_s = mapping.min_interval_s
            # proto3 0 = unset; substitute the connector default. There is
            # no way to express "no dead-man" by design.
            self._max_axis_age_s = (
                mapping.max_axis_age_s
                if mapping.max_axis_age_s > 0
                else _DEFAULT_MAX_AXIS_AGE_S
            )
            self._last_emit_at = 0.0
            self._emitting = False

        for axis_name, axis_cfg in mapping.axes.items():
            entity_id = axis_cfg.entity_id or self._connector_entity_id
            subject = axis_cfg.subject
            source_id = axis_cfg.source_id or "**"
            key = keelson.construct_pubsub_key(
                self._realm, entity_id, subject, source_id
            )
            logger.info(
                "control axis %s: subscribing to %s (unipolar=%s invert=%s)",
                axis_name,
                key,
                axis_cfg.unipolar,
                axis_cfg.invert,
            )
            normalised = ControlAxis(
                entity_id=entity_id,
                subject=subject,
                source_id=source_id,
                unipolar=axis_cfg.unipolar,
                invert=axis_cfg.invert,
            )
            sub = self._session.declare_subscriber(
                key,
                lambda sample, _axis=axis_name: self._on_sample(_axis, sample),
            )
            with self._lock:
                self._axes[axis_name] = _AxisRuntime(
                    name=axis_name,
                    config=normalised,
                    subscriber=sub,
                )

    def get_mapping(self) -> ControlAxisMapping:
        """Return the active mapping. ``max_axis_age_s`` reflects the
        *effective* dead-man window (i.e. the default substituted for a
        0 in the request) so the operator can see what's gating
        emission."""
        with self._lock:
            return ControlAxisMapping(
                axes={name: axis.config for name, axis in self._axes.items()},
                min_interval_s=self._min_interval_s,
                max_axis_age_s=self._max_axis_age_s,
            )

    def close(self) -> None:
        """Undeclare every axis subscriber. Idempotent."""
        self.set_mapping(ControlAxisMapping())

    # ---- internals ------------------------------------------------------

    def _on_sample(self, axis_name: str, sample: "Any") -> None:
        try:
            _, _, payload_bytes = keelson.uncover(bytes(sample.payload.to_bytes()))
            msg = TimestampedFloat()
            msg.ParseFromString(payload_bytes)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decode axis %s envelope", axis_name)
            return

        now = time.time()
        unit_values: Optional[dict[str, float]] = None
        with self._lock:
            axis = self._axes.get(axis_name)
            if axis is None:
                return
            axis.last_value = float(msg.value)
            axis.last_received_at = now

            # Throttle gate.
            if (
                self._min_interval_s > 0.0
                and (now - self._last_emit_at) < self._min_interval_s
            ):
                return

            # Dead-man: any axis with no sample yet, or with its last
            # sample older than the gate, blocks emission entirely. Fail
            # closed — partial freshness means upstream malfunction.
            stale_reason: Optional[str] = None
            for a in self._axes.values():
                if a.last_received_at == 0.0:
                    stale_reason = f"axis {a.name} has not yet published"
                    break
                age = now - a.last_received_at
                if age > self._max_axis_age_s:
                    stale_reason = (
                        f"axis {a.name} stale by {age:.2f}s "
                        f"(limit {self._max_axis_age_s:.2f}s)"
                    )
                    break
            if stale_reason is not None:
                if self._emitting:
                    logger.warning(
                        "control disengaged: %s; MCU emission stopped",
                        stale_reason,
                    )
                    self._emitting = False
                return

            if not self._emitting:
                logger.info(
                    "control engaged: forwarding to MCU "
                    "(axes=%s, dead-man=%.2fs)",
                    sorted(self._axes.keys()),
                    self._max_axis_age_s,
                )
                self._emitting = True

            unit_values = self._compute_unit_values_locked()
            self._last_emit_at = now

        # _emit runs outside the lock so a slow encoder/transport can't
        # block sample arrivals on other axes.
        if unit_values is not None:
            self._emit(unit_values)

    def _compute_unit_values_locked(self) -> dict[str, float]:
        """Map current per-axis raw values to unit [-1, 1] (or [0, 1] for
        unipolar). Caller must hold ``_lock``."""
        out: dict[str, float] = {}
        for axis in self._axes.values():
            if axis.last_value is None:
                continue
            out[axis.name] = _scale_axis_value(
                axis.last_value,
                unipolar=axis.config.unipolar,
                invert=axis.config.invert,
            )
        return out

    def _emit(self, unit_values: dict[str, float]) -> None:
        """Extension point for the MCU encoder. Stubbed today: just logs
        what would be forwarded. Once framing lands this becomes
        ``transport.write(framing.encode(encode_mcu_command(unit_values)))``
        and a publish of the same values under
        ``{source-id}/setpoint`` for telemetry.
        """
        logger.debug(
            "would forward to MCU (framing stubbed): %s",
            {k: f"{v:+.3f}" for k, v in sorted(unit_values.items())},
        )
