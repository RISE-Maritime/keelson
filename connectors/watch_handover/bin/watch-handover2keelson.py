#!/usr/bin/env python3
"""The vessel's answer to a watch handover.

A ROC watch handover is a briefing one operator signs over to another. When the
relief accepts, the record can be held at HANDOVER_STATUS_PENDING_VESSEL until the
vessel itself confirms it will accept remote operation by a new watch. This
connector is that confirmation (protocol-specification.md §7.5).

It does not form an opinion of its own. `operational_authority` is already "the
vessel's own veto on accepting remote control, computed on the vessel", published
by whichever Layer 3 aggregator this deployment runs. This reads the standing
verdict and compares it to a floor — see watch_handover/verdict.py.

THE KEY IS OUTSIDE THIS VESSEL'S NAMESPACE, DELIBERATELY.

The handover record, an Envelope(keelson.ChecklistHandover), lives at

    {checklist_realm}/@v0/{checklist_entity}/pubsub/checklist_handover/{handover_id}

— by default `rise/@v0/roc1`, the operations centre's own entity, because a
handover is a shared document several sites work on rather than telemetry from
one platform. That is outside this vessel's own key namespace, so the
subscription is explicit rather than derived from --realm/--entity-id. Zenoh
keys are strings; a connector may subscribe to anything it is told about.

(It was `crowsnest/@v0/checklist` until 2026-08-26. `crowsnest` is an
application, not a deployment, and `checklist` names a document rather than a
thing; the ROC entity names the tree — NOT the station, which is already carried
in the source id of every checklist key.)

The key is a SLOT (§2.1.1): one record per handover, overwritten by the offering
station, the relief's station and this connector in turn, with the handover id
as its last token. Who wrote a version is in the payload, not the key.

Idempotent by construction: it acts only on records at PENDING_VESSEL, so its own
write echoing back is ignored, and a record any station has already driven
terminal is left alone.
"""

import argparse
import json
import logging
import threading
import time

import zenoh

from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.scaffolding import (
    add_common_arguments,
    create_zenoh_config,
    declare_source_liveliness,
    put,
)

from watch_handover.verdict import (
    DEFAULT_MAX_AGE_S,
    DEFAULT_MIN_LEVEL,
    GATE_NO_AUTHORITY,
    LEVEL_NAMES,
    NON_AUTHORIZING,
    decide,
    level_name,
    to_vessel_verdict,
)

logger = logging.getLogger("watch-handover")

PENDING = ChecklistHandover.HANDOVER_STATUS_PENDING_VESSEL

#: How long to wait for a first `operational_authority` before answering a
#: handover with GATE_NO_AUTHORITY, in seconds.
#:
#: Both subscriptions are declared together, but they do not arrive together: the
#: router replays a retained handover record at once, while the aggregator's next
#: authority sample is up to one publish period away — 10 s at its 0.1 Hz default.
#: Without this window, restarting mid-handover refuses a perfectly healthy vessel
#: because this process had not heard from it yet, and `refused` is terminal.
#:
#: Only GATE_NO_AUTHORITY waits. Every other verdict is answered the moment it is
#: reached, so the grace costs nothing whenever authority is actually flowing.
DEFAULT_STARTUP_GRACE_S = 12.0

#: How often the worker re-examines pending handovers, in seconds.
DEFAULT_ANSWER_INTERVAL_S = 2.0

#: How many times to publish an answer before giving up.
#:
#: The put is fire-and-forget under congestion_control DROP, so a shed sample
#: leaves the record at PENDING_VESSEL with nobody coming back to it. The
#: record leaving PENDING_VESSEL is the only acknowledgement available, so the
#: answer is repeated until that is observed. Bounded rather than endless: if the
#: record never changes, the storage is misconfigured and putting for ever would
#: hide that rather than report it.
DEFAULT_ANSWER_MAX_ATTEMPTS = 5


def handover_key(checklist_realm, checklist_entity, handover_id="*"):
    return f"{checklist_realm}/@v0/{checklist_entity}/pubsub/checklist_handover/{handover_id}"


def liveliness_source_id(entity_id):
    """The source id this process is present as.

    It carries the vessel because the token is declared under the ROC entity,
    not the vessel's — two connectors serving two vessels would otherwise be
    indistinguishable. Multi-chunk source ids are explicitly allowed; the source
    id is the remainder of the key.
    """
    return f"watch_handover/{entity_id}"


def authority_key(realm, entity_id):
    return f"{realm}/@v0/{entity_id}/pubsub/operational_authority/**"


def authority_source_of(key_expr):
    """Source id of an `operational_authority` key, for telling publishers apart.

    Falls back to the whole key when it will not parse: an unparseable key is
    still a distinct publisher, and collapsing it onto a shared bucket would
    silently merge two aggregators back into the last-writer-wins this exists to
    avoid.
    """
    import keelson

    try:
        return keelson.parse_pubsub_key(str(key_expr))["source_id"]
    except Exception:
        return str(key_expr)


class WatchHandoverResponder:
    def __init__(self, session, args):
        self.session = session
        self.args = args
        # Latest reading PER SOURCE, as {source_id: (authority, monotonic_at)}.
        #
        # Keyed by source because `operational_authority/**` can carry more than
        # one publisher, and caching whichever arrived last made the verdict flap
        # with arrival order — two aggregators disagreeing would confirm or refuse
        # the same vessel depending on which sample happened to land second.
        #
        # Receipt time is measured on arrival rather than read from the message's
        # own `timestamp`: the failure this catches is the aggregator stopping,
        # which local receipt sees directly, and it cannot be confused by vessel
        # clock skew. `monotonic` so an NTP step cannot make a reading look fresh.
        self._authority = {}
        self._lock = threading.Lock()
        # Handovers seen at PENDING_VESSEL and not yet observed to have left it,
        # as {handover_id: attempts_published}. A record leaving PENDING is what
        # removes it — see `on_handover` — which is both how a landed answer is
        # acknowledged and what keeps this bounded. It used to be a set that only
        # ever grew.
        self._pending = {}
        # The last body seen for each pending id, so a retry republishes the
        # station's record rather than a stale copy taken at first sight.
        self._records = {}
        self._started_at = time.monotonic()
        self._stop = threading.Event()

    # ── the vessel's standing verdict ────────────────────────────────────
    def on_authority(self, sample):
        try:
            _, _, payload = keelson_uncover(sample)
            authority = OperationalAuthority()
            authority.ParseFromString(payload)
        except Exception:
            logger.exception(
                "could not decode operational_authority on %s", sample.key_expr
            )
            return
        source_id = authority_source_of(sample.key_expr)
        if self.args.authority_source_id and source_id != self.args.authority_source_id:
            logger.debug("ignoring authority from %s (pinned elsewhere)", source_id)
            return
        with self._lock:
            self._authority[source_id] = (authority, time.monotonic())
        logger.debug(
            "authority from %s now %s", source_id, level_name(int(authority.level))
        )

    def governing_authority(self):
        """The reading a verdict is taken on, and its age.

        THE LOWEST LEVEL AMONG FRESH READINGS, not the newest. `operational_authority`
        is the vessel's veto, so an aggregator reporting a constraint IS the vessel
        being constrained — taking the minimum is the only selection that cannot be
        talked out of a refusal by a second, cheerier publisher.

        When nothing is fresh the freshest stale reading is returned instead, so
        GATE_STALE_AUTHORITY fires with a meaningful age rather than collapsing into
        the weaker GATE_NO_AUTHORITY.
        """
        now = time.monotonic()
        with self._lock:
            readings = [(a, now - at) for a, at in self._authority.values()]
        if not readings:
            return None, None

        max_age_s = self.args.authority_max_age_s
        fresh = [
            (a, age) for a, age in readings if max_age_s is None or age <= max_age_s
        ]
        if fresh:
            return min(fresh, key=lambda pair: int(getattr(pair[0], "level", 0) or 0))
        return min(readings, key=lambda pair: pair[1])

    # ── handover records ─────────────────────────────────────────────────
    def on_handover(self, sample):
        """Classify, never answer.

        Answering here is what produced the startup race: the router replays a
        retained record the instant the subscriber is declared, which can be well
        before the first `operational_authority` arrives. The verdict is taken by
        the worker instead, which can wait.
        """
        record = decode_handover(sample)
        if record is None:
            return

        handover_id = record.handover_id
        if not handover_id:
            return

        # Only the pending phase is ours. Anything else — offered, or already
        # driven terminal by a station — is somebody else's business, and this
        # test is also what makes our own echo a no-op.
        #
        # A record that has LEFT pending is how this process learns its answer
        # landed: dropping it here is the acknowledgement the fire-and-forget put
        # cannot give, and it is what stops `_pending` growing without bound.
        if record.status != PENDING:
            with self._lock:
                self._pending.pop(handover_id, None)
                self._records.pop(handover_id, None)
            return

        vessel = record.vessel
        # The realm is matched only when the record states one: a station that
        # leaves it empty is naming the entity in its own realm, which is ours.
        if vessel.entity_id != self.args.entity_id or (
            vessel.realm and vessel.realm != self.args.realm
        ):
            logger.debug(
                "handover %s names %s/%s, not us",
                handover_id,
                vessel.realm,
                vessel.entity_id,
            )
            return

        with self._lock:
            if handover_id not in self._pending:
                self._pending[handover_id] = 0
                self._records[handover_id] = record

    # ── answering ────────────────────────────────────────────────────────
    def run(self):
        """Answer pending handovers until stopped.

        A loop rather than a reply-on-receipt because both fixes need one: waiting
        out the startup grace, and republishing an answer that was dropped.
        """
        while not self._stop.wait(self.args.answer_interval_s):
            try:
                self.answer_pending()
            except Exception:
                logger.exception("answer pass failed")

    def stop(self):
        self._stop.set()

    def answer_pending(self):
        with self._lock:
            outstanding = sorted(self._pending.items())
        for handover_id, attempts in outstanding:
            self.answer_one(handover_id, attempts)

    def answer_one(self, handover_id, attempts):
        with self._lock:
            record = self._records.get(handover_id)
        if record is None:
            return

        authority, age_s = self.governing_authority()
        confirmed, verdict = decide(
            authority,
            self.args.min_level,
            age_s=age_s,
            max_age_s=self.args.authority_max_age_s,
        )

        # Silence during the grace window is "not heard from yet", not "has no
        # authority". Refusing here would strand a healthy vessel on a restart,
        # and the refusal is terminal, so it is the one verdict worth waiting on.
        if verdict.get("gate") == GATE_NO_AUTHORITY and not attempts:
            waited = time.monotonic() - self._started_at
            if waited < self.args.startup_grace_s:
                logger.debug(
                    "handover %s: no authority yet, %.1fs into the %.0fs grace",
                    handover_id,
                    waited,
                    self.args.startup_grace_s,
                )
                return

        if attempts >= self.args.answer_max_attempts:
            with self._lock:
                self._pending.pop(handover_id, None)
                self._records.pop(handover_id, None)
            logger.error(
                "handover %s still reads %s after %d published answers — giving up. "
                "The record is not coming back changed, which usually means the "
                "router has no storage for this key.",
                handover_id,
                PENDING,
                attempts,
            )
            return

        answered = answer_record(record, confirmed, verdict, authority)

        with self._lock:
            self._pending[handover_id] = attempts + 1

        # READ-MODIFY-WRITE, and zenoh gives no way to make it atomic. Any field a
        # station wrote between our read and this put is overwritten by the copy we
        # took. Tolerable because the fields this process sets are its own and no
        # station writes them, and because the window is one pass of the worker —
        # but it is a lost update, not an absence of one, and a future writer of
        # this key should know that rather than discover it.
        # `put` takes the QoS from the subject in the key: `elevated` for
        # checklist_handover (messages/qos.yaml).
        put(
            self.session,
            handover_key(
                self.args.checklist_realm, self.args.checklist_entity, handover_id
            ),
            keelson_enclose(answered.SerializeToString()),
        )
        # A refusal is logged louder than a confirmation on purpose: it strands
        # the outgoing operator on a watch they were trying to hand over, and how
        # often it happens is the open question about where `--min-level` belongs.
        # `gate` is included so those can be counted without reading the prose.
        logger.log(
            logging.INFO if confirmed else logging.WARNING,
            "handover %s %s [%s]%s — %s",
            handover_id,
            "CONFIRMED" if confirmed else "REFUSED",
            verdict.get("gate"),
            f" (retry {attempts})" if attempts else "",
            verdict.get("reason"),
        )


def decode_handover(sample):
    """The ChecklistHandover in a sample, or None if it is not one.

    A record written before keelson#231 is raw JSON. It is named as such rather
    than reported as noise, because it is the one undecodable payload with an
    explanation: a station that never moved to the subject, or a storage holding
    a record from before it did. Neither is answered.
    """
    raw = bytes(sample.payload.to_bytes())
    try:
        _, _, payload = keelson_uncover_bytes(raw)
        record = ChecklistHandover()
        record.ParseFromString(payload)
        return record
    except Exception:
        pass
    try:
        json.loads(raw.decode("utf-8"))
    except Exception:
        logger.warning("undecodable handover payload on %s", sample.key_expr)
    else:
        logger.warning(
            "handover on %s is raw JSON, the form from before keelson#231; it is "
            "not answered. The station that wrote it must publish "
            "keelson.ChecklistHandover.",
            sample.key_expr,
        )
    return None


def answer_record(record, confirmed, verdict, authority, now_ns=None):
    """The record with the vessel's answer on it (§7.5).

    A copy of the station's record, so every field this process does not own
    goes back as it came, unknown ones included. `accepted_at` is the relief's
    instant and stays; `refused_by` names a PERSON and is never set for a vessel
    refusal, which is what `refusal_source` is for.
    """
    answered = ChecklistHandover()
    answered.CopyFrom(record)
    now_ns = time.time_ns() if now_ns is None else now_ns

    answered.status = (
        ChecklistHandover.HANDOVER_STATUS_ACCEPTED
        if confirmed
        else ChecklistHandover.HANDOVER_STATUS_REFUSED
    )
    # `timestamp` is when this version was written: the §7.5.2 tiebreak.
    answered.timestamp.FromNanoseconds(now_ns)
    answered.vessel_confirmed_at.FromNanoseconds(now_ns)
    answered.vessel_verdict.CopyFrom(to_vessel_verdict(verdict, authority))
    if not confirmed:
        answered.refused_at.FromNanoseconds(now_ns)
        answered.refusal_source = ChecklistHandover.REFUSAL_SOURCE_VESSEL
        answered.refusal_reason = verdict.get("reason") or ""
    return answered


def keelson_enclose(payload):
    import keelson

    return keelson.enclose(payload)


def keelson_uncover_bytes(raw):
    import keelson

    return keelson.uncover(raw)


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
        default="rise",
        help="Realm the checklist tree lives under. The handover key is NOT under --realm; "
        "see the module docstring.",
    )
    parser.add_argument(
        "--checklist-entity",
        default="roc1",
        help="Entity the checklist tree lives under — the operations centre, not this vessel "
        "and not the operator's station. Must match what the ROC clients and the router's "
        "storage are configured with, or the handover is published where nobody is looking.",
    )
    parser.add_argument(
        "--min-level",
        type=int,
        default=DEFAULT_MIN_LEVEL,
        choices=sorted(LEVEL_NAMES),
        help="Lowest authority level that confirms a handover. "
        + ", ".join(f"{k}={v}" for k, v in sorted(LEVEL_NAMES.items()))
        + f". NOTE: {sorted(NON_AUTHORIZING)} are non-authorizing whatever this is set to, "
        "so 0, 1 and 2 all behave identically — the floor only decides anything at 3 or "
        "above, which is why 3 is the default. Setting 2 or less is not a lower bar, it is "
        "NO bar: every refusal it can produce is the protocol-mandated one.",
    )
    parser.add_argument(
        "--authority-max-age-s",
        type=float,
        default=DEFAULT_MAX_AGE_S,
        help="Refuse rather than trust an operational_authority reading older than this. "
        "Guards against the aggregator dying while the vessel reads a high level, which "
        "would otherwise confirm handovers forever against a frozen value. "
        f"Default {DEFAULT_MAX_AGE_S:g}s; 0 disables the check.",
    )
    parser.add_argument(
        "--authority-source-id",
        default=None,
        help="Read operational_authority from this source only. By default every "
        "source under the entity is read and the LOWEST level among fresh readings "
        "governs, since any aggregator reporting a constraint is the vessel being "
        "constrained. Pin a source when a deployment wants one aggregator to be "
        "authoritative.",
    )
    parser.add_argument(
        "--startup-grace-s",
        type=float,
        default=DEFAULT_STARTUP_GRACE_S,
        help="Wait this long for a first operational_authority before refusing a "
        "handover for the lack of one. The router replays a retained handover "
        "immediately while the aggregator's next sample is up to a publish period "
        "away, so without this a restart mid-handover refuses a healthy vessel — "
        f"terminally. Default {DEFAULT_STARTUP_GRACE_S:g}s; only this one gate waits.",
    )
    parser.add_argument(
        "--answer-interval-s",
        type=float,
        default=DEFAULT_ANSWER_INTERVAL_S,
        help=f"How often to re-examine pending handovers. Default {DEFAULT_ANSWER_INTERVAL_S:g}s.",
    )
    parser.add_argument(
        "--answer-max-attempts",
        type=int,
        default=DEFAULT_ANSWER_MAX_ATTEMPTS,
        help="Publish an answer at most this many times. The put is fire-and-forget "
        "under DROP, so an answer that is shed leaves the record pending with nobody "
        "returning to it; the record leaving PENDING_VESSEL is the only acknowledgement "
        f"available. Default {DEFAULT_ANSWER_MAX_ATTEMPTS}.",
    )
    args = parser.parse_args()
    # argparse cannot express "None means off" for a float, so spell it here.
    if args.authority_max_age_s <= 0:
        args.authority_max_age_s = None

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=args.log_level,
    )

    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )
    with zenoh.open(zconf) as session:
        responder = WatchHandoverResponder(session, args)

        akey = authority_key(args.realm, args.entity_id)
        hkey = handover_key(args.checklist_realm, args.checklist_entity)
        source_id = liveliness_source_id(args.entity_id)

        # Source-level liveliness: "a watch_handover responder for this vessel is
        # present". This process `put`s the answered record, so it has a producing
        # role and §5.4 makes the token mandatory rather than optional. Without it a
        # running responder is indistinguishable from one that was never started,
        # and a consumer cannot warn before relying on it.
        #
        # DECLARED UNDER THE ROC ENTITY, not the vessel's. That is where this
        # process actually produces — the answered record lands on
        # `{checklist_realm}/@v0/{checklist_entity}/pubsub/checklist_handover/...`.
        # A token under the vessel would claim a producing role in a key-space
        # where this writes nothing.
        #
        # SOURCE-LEVEL ONLY, no subject-level token — the rule, not an exception.
        # `checklist_handover/{handover_id}` is a SLOT key (§2.1.1): its last token
        # is the record, not a producer, and "a slot key has no producer and no
        # subject-level token" (§2.1.1, §5.2). Which handovers exist is learned
        # from the storage behind the key, never from liveliness.
        #
        # SUBSCRIBERS FIRST, token second. A consumer reads the token as "this
        # responder can hear a handover now", and without router storage a record
        # put before the subscriber exists is simply lost — the vessel never
        # answers and nothing says why.
        session.declare_subscriber(akey, responder.on_authority)
        session.declare_subscriber(hkey, responder.on_handover)
        with declare_source_liveliness(
            session, args.checklist_realm, args.checklist_entity, source_id
        ):
            logger.info("watching %s", akey)
            logger.info("answering %s for entity %s", hkey, args.entity_id)
            logger.info("confirming at %s or better", level_name(args.min_level))
            logger.info(
                "present as %s/@v0/%s/*/%s",
                args.checklist_realm,
                args.checklist_entity,
                source_id,
            )

            worker = threading.Thread(
                target=responder.run, name="watch-handover-answer", daemon=True
            )
            worker.start()

            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                logger.info("shutting down")
                responder.stop()
                worker.join(timeout=args.answer_interval_s * 2)


if __name__ == "__main__":
    main()
