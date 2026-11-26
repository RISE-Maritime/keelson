#!/usr/bin/env python3
"""The vessel's gate on the ROC mandate.

Holds her short of waters the ROC site on her watch holds no mandate for, and
says so on `mandate_gate` under her own entity. Everything it decides is in
mandate_gate/gate.py, which is pure; this is the wiring:

- **her own sensors**, under her entity: `location_fix`, `course_over_ground_deg`
  and `speed_over_ground_knots`, from any source. The newest reading of each
  counts, and a reading older than the config's `fix_max_age_s` (by this
  process's clock, on receipt) does not.
- **the watch**, from `checklist_handover` on the ROC's tree
  (`{checklist_realm}/@v0/{checklist_entity}`, as for watch_handover). Read
  from storage once at start, then followed. Both wire forms are read:
  keelson.ChecklistHandover in an Envelope, and the legacy JSON.
- **the gate's word**, `{realm}/@v0/{entity}/pubsub/mandate_gate/{source_id}`,
  every `--publish-interval-s` and at once whenever the state or the hold
  changes.

It asks for a hold and does not carry it out. Whoever steers her — her
navigation process, her autopilot, a simulator standing in for one — reads
`mandate_gate` and acts on a new `hold_id` once.
"""

import argparse
import json
import logging
import signal
import threading
import time

import zenoh

import keelson
from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover
from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix
from keelson.payloads.MandateGate_pb2 import MandateGate
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_pubsub_subject_liveliness,
    declare_source_liveliness,
)

from mandate_gate.gate import CLEAR, HOLDING, NO_FIX, Fix, Gate, load_config
from mandate_gate.watch import from_json, from_proto, watch_of

logger = logging.getLogger("mandate-gate")

SUBJECT = "mandate_gate"
DEFAULT_SOURCE_ID = "mandate-gate"
DEFAULT_PUBLISH_INTERVAL_S = 1.0
#: How often the decision is re-taken. Publishing is at the interval above, or
#: at once on a change, so this only bounds how late a change is noticed.
TICK_S = 0.25

_STATE = {
    CLEAR: MandateGate.STATE_CLEAR,
    HOLDING: MandateGate.STATE_HOLDING,
    NO_FIX: MandateGate.STATE_NO_FIX,
}


#: A station's `roc_status` beats every 10 s; three missed beats and it has gone.
ROC_SILENCE_S = 35.0


def roc_status_key(realm):
    """Every station's own roc_status: `{realm}/@v0/{entity}/pubsub/roc_status/{site}/{operator}`."""
    return f"{realm}/@v0/*/pubsub/roc_status/**"


def site_of(key: str) -> str | None:
    """The ROC site a roc_status key is from: the first chunk after the subject."""
    parts = str(key).split("/")
    try:
        i = parts.index("roc_status")
    except ValueError:
        return None
    return parts[i + 1] if len(parts) > i + 1 else None


def handover_key(checklist_realm, checklist_entity):
    return f"{checklist_realm}/@v0/{checklist_entity}/pubsub/checklist_handover/*"


def sensor_key(realm, entity_id, subject):
    return f"{realm}/@v0/{entity_id}/pubsub/{subject}/**"


def decode_handover(raw: bytes):
    """A handover Record from either wire form, or None."""
    if raw[:1] == b"{":
        return from_json(raw)
    try:
        _, _, payload = keelson.uncover(raw)
        handover = ChecklistHandover()
        handover.ParseFromString(payload)
    except Exception:
        return None
    return from_proto(handover) if handover.handover_id else None


class MandateGateRunner:
    def __init__(self, session, args, config):
        self.session = session
        self.args = args
        self.config = config
        self.gate = Gate(config)
        self.lock = threading.Lock()
        self.records = {}
        self.position = None  # (lat, lon, received_monotonic)
        self.cog = None  # (deg, received_monotonic)
        self.sog = None  # (knots, received_monotonic)
        self.key = keelson.construct_pubsub_key(
            args.realm, args.entity_id, SUBJECT, args.source_id
        )
        self._stop = threading.Event()
        self._last_sent = None
        #: ROC site -> when its roc_status was last heard (monotonic).
        self.roc_heard = {}

    # --- inputs ----------------------------------------------------------------------

    def on_handover(self, sample):
        record = decode_handover(bytes(sample.payload.to_bytes()))
        if record is None:
            logger.debug("not a handover record on %s", sample.key_expr)
            return
        with self.lock:
            self.records[record.handover_id] = record

    def on_roc_status(self, sample):
        site = site_of(sample.key_expr)
        if site:
            with self.lock:
                self.roc_heard[site] = time.monotonic()

    def roc_online(self, site: str, now: float) -> bool:
        """A station of `site` was heard inside ROC_SILENCE_S. No site, no ROC."""
        heard = self.roc_heard.get(site) if site else None
        return heard is not None and now - heard <= ROC_SILENCE_S

    def on_location(self, sample):
        try:
            _, _, payload = keelson.uncover(bytes(sample.payload.to_bytes()))
            fix = LocationFix()
            fix.ParseFromString(payload)
        except Exception:
            logger.debug("unreadable location_fix on %s", sample.key_expr)
            return
        with self.lock:
            self.position = (fix.latitude, fix.longitude, time.monotonic())

    def _float(self, sample):
        try:
            _, _, payload = keelson.uncover(bytes(sample.payload.to_bytes()))
            value = TimestampedFloat()
            value.ParseFromString(payload)
        except Exception:
            return None
        return value.value, time.monotonic()

    def on_cog(self, sample):
        reading = self._float(sample)
        if reading is not None:
            with self.lock:
                self.cog = reading

    def on_sog(self, sample):
        reading = self._float(sample)
        if reading is not None:
            with self.lock:
                self.sog = reading

    def load_stored_handovers(self):
        """The handovers already in storage, so a gate started mid-run knows the watch."""
        try:
            for reply in self.session.get(
                handover_key(self.args.checklist_realm, self.args.checklist_entity),
                timeout=5.0,
            ):
                if reply.ok is not None:
                    self.on_handover(reply.ok)
        except Exception:
            logger.warning(
                "could not read stored handovers; following live ones only",
                exc_info=True,
            )

    # --- the decision ----------------------------------------------------------------

    def _fix(self, now):
        fresh = self.config.fix_max_age_s
        if self.position is None or now - self.position[2] > fresh:
            return None
        cog = (
            self.cog[0] if self.cog is not None and now - self.cog[1] <= fresh else None
        )
        sog = (
            self.sog[0] if self.sog is not None and now - self.sog[1] <= fresh else None
        )
        return Fix(self.position[0], self.position[1], cog, sog)

    def step(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            watch = watch_of(
                self.records, self.args.entity_id, self.config.initial_watch
            )
            report = self.gate.update(
                self._fix(now),
                watch,
                roc_online=self.roc_online(watch.site, now),
                now_s=now,
            )
        changed = (
            self._last_sent is None
            or (report.state, report.hold_id, report.watch) != self._last_sent[0]
        )
        due = (
            self._last_sent is None
            or now - self._last_sent[1] >= self.args.publish_interval_s
        )
        if changed or due:
            if changed:
                logger.info(
                    "%s (watch %s, hold %d): %s",
                    report.state,
                    report.watch.site,
                    report.hold_id,
                    report.reason,
                )
            self.publish(report)
            self._last_sent = ((report.state, report.hold_id, report.watch), now)
        return report

    def publish(self, report):
        msg = MandateGate(
            state=_STATE[report.state],
            reason=report.reason,
            watch_site=report.watch.site,
            handover_id=report.watch.handover_id,
            zone_ahead=report.zone_ahead,
            hold_id=report.hold_id,
        )
        msg.timestamp.FromNanoseconds(time.time_ns())
        if report.distance_to_boundary_m is not None:
            msg.distance_to_boundary_m = report.distance_to_boundary_m
        msg.stop = report.stop
        if report.roc_absent_s is not None:
            msg.roc_absent_s = report.roc_absent_s
        if report.hold is not None:
            msg.hold.latitude = report.hold.lat
            msg.hold.longitude = report.hold.lon
            msg.hold.radius_m = report.hold.radius_m
        self.session.put(self.key, keelson.enclose(msg.SerializeToString()))

    def run(self):
        while not self._stop.wait(TICK_S):
            try:
                self.step()
            except Exception:
                logger.exception("gate step failed")

    def stop(self):
        self._stop.set()


def main():
    parser = argparse.ArgumentParser(
        prog="mandate-gate2keelson",
        description="Hold the vessel short of waters the ROC on her watch has no mandate for.",
    )
    add_common_arguments(parser)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument(
        "-e",
        "--entity-id",
        type=str,
        required=True,
        help="The vessel: her sensors are read and the gate's word is published under this "
        "entity, and it is matched against a handover record's vessel.entity_id.",
    )
    parser.add_argument("-s", "--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument(
        "--checklist-realm",
        default="rise",
        help="Realm the handover records live under (as for watch-handover2keelson).",
    )
    parser.add_argument(
        "--checklist-entity",
        default="roc1",
        help="Entity the handover records live under: the operations centre's tree.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="JSON: initial_watch, areas [{id, roc_sites, polygon [[lat, lon], ...]}], and "
        "optionally margin_m, loiter_radius_m, reaction_s, stopping {speed_knots, distance_m}, "
        "warn_s, fix_max_age_s, min_course_knots. See the README.",
    )
    parser.add_argument(
        "--publish-interval-s",
        type=float,
        default=DEFAULT_PUBLISH_INTERVAL_S,
        help=f"How often to publish when nothing changes. Default {DEFAULT_PUBLISH_INTERVAL_S:g}s.",
    )
    args = parser.parse_args()

    def _terminate(*_):
        # A container is stopped with SIGTERM: leave as on Ctrl-C, undeclaring the tokens.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )

    try:
        with open(args.config, encoding="utf-8") as fh:
            config = load_config(json.load(fh))
    except (OSError, ValueError) as exc:
        # ConfigError is a ValueError, and so is a JSON syntax error.
        parser.error(f"--config {args.config}: {exc}")

    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )
    with zenoh.open(zconf) as session:
        runner = MandateGateRunner(session, args, config)
        session.declare_subscriber(
            sensor_key(args.realm, args.entity_id, "location_fix"), runner.on_location
        )
        session.declare_subscriber(
            sensor_key(args.realm, args.entity_id, "course_over_ground_deg"),
            runner.on_cog,
        )
        session.declare_subscriber(
            sensor_key(args.realm, args.entity_id, "speed_over_ground_knots"),
            runner.on_sog,
        )
        session.declare_subscriber(
            handover_key(args.checklist_realm, args.checklist_entity),
            runner.on_handover,
        )
        if config.without_roc_s is not None:
            # Whether a ROC on her watch is online, for her `without_roc` limit.
            session.declare_subscriber(roc_status_key(args.realm), runner.on_roc_status)
        runner.load_stored_handovers()
        with (
            declare_source_liveliness(
                session, args.realm, args.entity_id, args.source_id
            ),
            declare_pubsub_subject_liveliness(
                session, args.realm, args.entity_id, args.source_id, [SUBJECT]
            ),
        ):
            logger.info(
                "gating %s/@v0/%s, watch from %s",
                args.realm,
                args.entity_id,
                handover_key(args.checklist_realm, args.checklist_entity),
            )
            logger.info(
                "initial watch %s; %d mandate areas",
                config.initial_watch,
                len(config.areas),
            )
            runner.step()
            worker = threading.Thread(
                target=runner.run, name="mandate-gate", daemon=True
            )
            worker.start()
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                logger.info("shutting down")
                runner.stop()
                worker.join(timeout=2.0)


if __name__ == "__main__":
    main()
