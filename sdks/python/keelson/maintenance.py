"""A gatekeeper's view of `maintenance_state/{vessel_id}` (protocols/maintenance.yaml).

The authority manager is the one writer of the record and republishes it at its heartbeat. A gatekeeper
-- the platform connector's `set_config`, container_control's verbs, a vehicle connector's arm -- reads
it live and judges it on its OWN clock, from local receipt:

- a record older than ``max_age_ms`` is stale, and a stale or unheard record is in no state at all: it
  satisfies nothing, so the gatekeeper refuses;
- a refusal names what was required and what was heard, so the operator reads which step of the
  procedure is holding them.

``MaintenanceWatch`` is pure (no zenoh, no clock of its own) so the rule is testable without a bus;
``subscribe`` wires it to a session.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from keelson import construct_pubsub_key, uncover
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.payloads.MaintenanceState_pb2 import MaintenanceState

logger = logging.getLogger(__name__)

INACTIVE = MaintenanceState.State.STATE_INACTIVE
MAINTENANCE = MaintenanceState.State.STATE_MAINTENANCE

#: Three of the manager's 5 s heartbeats: one lost sample is not a stale procedure.
DEFAULT_MAX_AGE_MS = 15_000


def state_name(state: int) -> str:
    """``STATE_MAINTENANCE`` -> ``MAINTENANCE``; an unknown number stays a number."""
    try:
        return MaintenanceState.State.Name(state).removeprefix("STATE_")
    except ValueError:
        return f"state {state}"


class MaintenanceRefused(Exception):
    """A write or an action the vessel's maintenance procedure does not allow now. ``error_code`` is the
    typed ``ErrorResponse`` code a responder replies with: INVALID_STATE, because the request is well
    formed and would be accepted in another state."""

    error_code = ErrorResponse.Code.INVALID_STATE


@dataclass(frozen=True)
class Heard:
    state: int
    maintenance_id: str
    revision: int
    heard_ms: int


class MaintenanceWatch:
    """The latest `maintenance_state` of one vessel, stamped on local receipt."""

    def __init__(
        self,
        vessel_id: str,
        max_age_ms: int = DEFAULT_MAX_AGE_MS,
        now_ms: Callable[[], int] = lambda: time.monotonic_ns() // 1_000_000,
    ):
        self.vessel_id = vessel_id
        self.max_age_ms = int(max_age_ms)
        self._now_ms = now_ms
        self._lock = threading.Lock()
        self._heard: Optional[Heard] = None

    def observe(self, record: MaintenanceState) -> None:
        """One record off the bus. A record for another vessel is not this vessel's procedure."""
        if record.vessel_id and record.vessel_id != self.vessel_id:
            logger.warning(
                "maintenance_state for %r on %r's key: ignored",
                record.vessel_id,
                self.vessel_id,
            )
            return
        with self._lock:
            self._heard = Heard(
                int(record.state),
                record.maintenance_id,
                int(record.revision),
                self._now_ms(),
            )

    def heard(self) -> Optional[Heard]:
        with self._lock:
            return self._heard

    def refusal(self, required: int, what: str) -> Optional[str]:
        """None when the vessel's procedure is fresh and in ``required``; otherwise why ``what`` is
        refused."""
        need = state_name(required)
        h = self.heard()
        if h is None:
            return f"{what} needs maintenance_state {need}: no maintenance_state heard for {self.vessel_id}"
        age = self._now_ms() - h.heard_ms
        if age > self.max_age_ms:
            return (
                f"{what} needs maintenance_state {need}: the last record for {self.vessel_id} "
                f"is {age} ms old (stale after {self.max_age_ms} ms)"
            )
        if h.state != required:
            return f"{what} needs maintenance_state {need}: {self.vessel_id} is {state_name(h.state)}"
        return None

    def require(self, required: int, what: str) -> Heard:
        """The record that allows ``what``, or :class:`MaintenanceRefused` naming why not."""
        why = self.refusal(required, what)
        if why is not None:
            raise MaintenanceRefused(why)
        return self.heard()


def subscribe(session, realm: str, watch: MaintenanceWatch):
    """Declare the live subscriber feeding ``watch``. Never read from storage: a stored record says
    nothing about a live manager, and its heartbeat arrives within seconds."""
    key = construct_pubsub_key(
        realm, watch.vessel_id, "maintenance_state", watch.vessel_id
    )

    def _on(sample):
        try:
            record = MaintenanceState()
            record.ParseFromString(uncover(sample.payload.to_bytes())[2])
            watch.observe(record)
        except Exception:  # pylint: disable=broad-except
            logger.exception("could not read maintenance_state on %s", sample.key_expr)

    logger.info("Maintenance gate: %s", key)
    return session.declare_subscriber(key, _on)
