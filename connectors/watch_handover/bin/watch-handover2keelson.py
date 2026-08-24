#!/usr/bin/env python3
"""The vessel's answer to a watch handover.

A ROC watch handover is a briefing one operator signs over to another. When the
relief accepts, the record can be held at `pending_vessel` until the vessel
itself confirms it will accept remote operation by a new watch. This connector
is that confirmation.

It does not form an opinion of its own. `operational_authority` is already "the
vessel's own veto on accepting remote control, computed on the vessel", published
by whichever Layer 3 aggregator this deployment runs. This reads the standing
verdict and compares it to a floor — see watch_handover/verdict.py.

TWO THINGS ABOUT THE KEY ARE UNUSUAL, AND BOTH ARE DELIBERATE.

The handover record lives at

    {checklist_realm}/@v0/checklist/pubsub/checklist_handover/{handover_id}

— realm `crowsnest`, entity `checklist`, because a handover is a shared document
several sites work on rather than telemetry from one platform. That is outside
this vessel's own key namespace, so the subscription is explicit rather than
derived from --realm/--entity-id. Zenoh keys are strings; a connector may
subscribe to anything it is told about.

And the payload is raw JSON, not a keelson Envelope, because `checklist_handover`
is a PROVISIONAL subject pending RISE-Maritime/keelson#218. An unknown subject
token is precisely what makes a consumer's decode fall through to JSON instead
of failing to unwrap an envelope. When #218 lands and the subject becomes
keelson-native, both of these go away and this connector should follow.

Idempotent by construction: it acts only on records in `pending_vessel`, so its
own write echoing back is ignored, and a record any station has already driven
terminal is left alone.
"""

import argparse
import json
import logging
import threading

import zenoh

from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.scaffolding import add_common_arguments, create_zenoh_config

from watch_handover.verdict import DEFAULT_MIN_LEVEL, LEVEL_NAMES, decide, level_name

logger = logging.getLogger("watch-handover")

PENDING = "pending_vessel"


def handover_key(checklist_realm, handover_id="*"):
    return f"{checklist_realm}/@v0/checklist/pubsub/checklist_handover/{handover_id}"


def authority_key(realm, entity_id):
    return f"{realm}/@v0/{entity_id}/pubsub/operational_authority/**"


class WatchHandoverResponder:
    def __init__(self, session, args):
        self.session = session
        self.args = args
        self._authority = None
        self._lock = threading.Lock()
        # Records this process has already answered. The router replays the key
        # on every reconnect, and answering twice would stamp a second, later
        # `vesselConfirmedAt` over the real one.
        self._answered = set()

    # ── the vessel's standing verdict ────────────────────────────────────
    def on_authority(self, sample):
        try:
            _, _, payload = keelson_uncover(sample)
            authority = OperationalAuthority()
            authority.ParseFromString(payload)
        except Exception:
            logger.exception("could not decode operational_authority on %s", sample.key_expr)
            return
        with self._lock:
            self._authority = authority
        logger.debug("authority now %s", level_name(int(authority.level)))

    # ── handover records ─────────────────────────────────────────────────
    def on_handover(self, sample):
        try:
            record = json.loads(bytes(sample.payload.to_bytes()).decode("utf-8"))
        except Exception:
            logger.warning("undecodable handover payload on %s", sample.key_expr)
            return
        if not isinstance(record, dict):
            return

        handover_id = record.get("handoverId")
        if not handover_id:
            return

        # Only the pending phase is ours. Anything else — offered, or already
        # driven terminal by a station — is somebody else's business, and this
        # test is also what makes our own echo a no-op.
        if record.get("status") != PENDING:
            return

        vessel = record.get("vessel") or {}
        if vessel.get("entityId") != self.args.entity_id:
            logger.debug("handover %s names %s, not us", handover_id, vessel.get("entityId"))
            return

        if handover_id in self._answered:
            return

        with self._lock:
            authority = self._authority
        confirmed, verdict = decide(authority, self.args.min_level)

        now = keelson_now_iso()
        answered = dict(record)
        answered["status"] = "accepted" if confirmed else "refused"
        answered["vesselConfirmedAt"] = now
        answered["vesselVerdict"] = verdict
        answered["updatedAt"] = now
        if not confirmed:
            answered["refusedAt"] = now
            answered["refusalReason"] = verdict.get("reason")

        self._answered.add(handover_id)
        self.session.put(
            handover_key(self.args.checklist_realm, handover_id),
            json.dumps(answered).encode("utf-8"),
            encoding=zenoh.Encoding.APPLICATION_JSON,
        )
        logger.info(
            "handover %s %s — %s",
            handover_id,
            "CONFIRMED" if confirmed else "REFUSED",
            verdict.get("reason"),
        )


def keelson_now_iso():
    """UTC now as the ISO string the record uses. Matches `new Date().toISOString()`."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def keelson_uncover(sample):
    """Uncover a keelson envelope from a sample."""
    import keelson

    return keelson.uncover(bytes(sample.payload.to_bytes()))


def main():
    parser = argparse.ArgumentParser(
        prog="watch-handover2keelson",
        description="Confirm or refuse a ROC watch handover from this vessel's operational_authority.",
    )
    add_common_arguments(parser)
    # The vessel this connector answers FOR. `--realm`/`--entity-id` scope the
    # operational_authority it reads and are matched against the handover
    # record's `vessel.entityId`; they are NOT where the handover key lives.
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument(
        "--checklist-realm",
        default="crowsnest",
        help="Realm the checklist tree lives under. The handover key is NOT under --realm; "
        "see the module docstring.",
    )
    parser.add_argument(
        "--min-level",
        type=int,
        default=DEFAULT_MIN_LEVEL,
        choices=sorted(LEVEL_NAMES),
        help="Lowest authority level that confirms a handover. "
        + ", ".join(f"{k}={v}" for k, v in sorted(LEVEL_NAMES.items())),
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=args.log_level,
    )

    zconf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)
    with zenoh.open(zconf) as session:
        responder = WatchHandoverResponder(session, args)

        akey = authority_key(args.realm, args.entity_id)
        hkey = handover_key(args.checklist_realm)
        session.declare_subscriber(akey, responder.on_authority)
        session.declare_subscriber(hkey, responder.on_handover)
        logger.info("watching %s", akey)
        logger.info("answering %s for entity %s", hkey, args.entity_id)
        logger.info("confirming at %s or better", level_name(args.min_level))

        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            logger.info("shutting down")


if __name__ == "__main__":
    main()
