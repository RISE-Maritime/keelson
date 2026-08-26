#!/usr/bin/env python3

"""Entity health connector.

Subscribes to a set of Zenoh key expressions, measures publication rate
and validates payload content against declarative expectations, and
publishes a keelson.EntityHealth message on the `entity_health` subject.

Reconfigurable at runtime via the Configurable RPC interface.
"""

# pylint: disable=duplicate-code

import sys
import json
import time
import logging
import argparse
import threading
from pathlib import Path

import zenoh
from jsonschema import validate, ValidationError

import keelson
from keelson import (
    construct_pubsub_key,
    enclose,
    get_subject_from_pubsub_key,
    parse_pubsub_key,
    parse_source_liveliness_key,
)
from keelson.payloads.EntityHealth_pb2 import (
    CheckResult,
    EntityHealth,
    SourceHealth,
    SubjectHealth,
)
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    declare_publisher,
    make_configurable,
)

# Add package dir to path so we can import the entity_health package
# alongside the bin script when running from source.
_PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_ROOT))
from entity_health.evaluator import (  # noqa: E402
    Band,
    ContentRule,
    Evaluator,
    Expectation,
    SourceLiveliness,
    evaluate_grouped,
    parse_level,
    token_covers_source,
)

logger = logging.getLogger("entity_health")

_SUBJECT_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "inactive_after_s": {"type": "number", "exclusiveMinimum": 0},
        "window_s": {"type": "number", "exclusiveMinimum": 0},
        "publication_rate_hz": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["level"],
                "properties": {
                    "level": {"type": "string"},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
        "publication_rate_default_level": {"type": "string"},
        "require_liveliness": {"type": "boolean"},
        "content_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "bands"],
                "properties": {
                    "field": {"type": "string"},
                    "bands": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["level"],
                            "properties": {
                                "level": {"type": "string"},
                                "min": {"type": "number"},
                                "max": {"type": "number"},
                                "equals": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "number"},
                                        {"type": "boolean"},
                                        {
                                            "type": "array",
                                            "items": {
                                                "anyOf": [
                                                    {"type": "string"},
                                                    {"type": "number"},
                                                    {"type": "boolean"},
                                                ]
                                            },
                                        },
                                    ]
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "default_level": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "title": "Entity Health Config",
    "properties": {
        "publish_rate_hz": {"type": "number", "exclusiveMinimum": 0},
        "realm": {"type": "string"},
        "entity_id": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "subjects"],
                "properties": {
                    "name": {"type": "string"},
                    "subjects": {
                        "type": "array",
                        "items": _SUBJECT_SCHEMA,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "required": ["sources"],
    "additionalProperties": False,
}

# Module-level state (cleared between tests). Data subscribers and
# evaluators are keyed by `(source_name, subject_name)`. Liveliness is
# tracked per-SOURCE: two Zenoh liveliness subscribers each, feeding a
# shared `SourceLiveliness` that every subject Evaluator for that source
# references. Both subscriptions are ENTITY-WIDE (`pubsub/*/**` and
# `*/**`), not scoped to the source — a token covers a source by segment
# prefix, which a key expression cannot express — and the handlers apply
# `token_covers_source` to decide which sources each sample speaks for.
# Coverage grants *presence* only; *advertisement* additionally requires the
# token to name the source exactly. See _make_pubsub_liveliness_handler.
PUBLISHERS: dict[str, zenoh.Publisher] = {}
SUBSCRIBERS: dict[tuple[str, str], zenoh.Subscriber] = {}
EVALUATORS: dict[tuple[str, str], Evaluator] = {}
SOURCE_LIVELINESS: dict[str, SourceLiveliness] = {}
# source_name -> (pubsub_wildcard_subscriber, source_level_subscriber)
SOURCE_LIVELINESS_SUBSCRIBERS: dict[str, tuple[zenoh.Subscriber, zenoh.Subscriber]] = {}
# source_name -> (pubsub_wildcard_key, source_level_key) — used to detect
# key changes across reconfig (realm/entity_id changed).
SOURCE_LIVELINESS_KEYS: dict[str, tuple[str, str]] = {}
CONFIG: dict = {}
STATE_LOCK = threading.Lock()
SESSION: zenoh.Session | None = None
ARGS: argparse.Namespace | None = None


def _content_rule_from_dict(r: dict) -> ContentRule:
    bands = [
        Band(
            level=parse_level(b["level"]),
            min=b.get("min"),
            max=b.get("max"),
            equals=b.get("equals"),
        )
        for b in r["bands"]
    ]
    kwargs: dict = {"field": r["field"], "bands": bands}
    if "default_level" in r:
        kwargs["default_level"] = parse_level(r["default_level"])
    return ContentRule(**kwargs)


def _expectation_from_dict(d: dict) -> Expectation:
    publication_rate_hz = [
        Band(level=parse_level(b["level"]), min=b.get("min"), max=b.get("max"))
        for b in d.get("publication_rate_hz", [])
    ]
    kwargs: dict = {
        "name": d["name"],
        "inactive_after_s": float(d.get("inactive_after_s", 10.0)),
        "window_s": float(d.get("window_s", 10.0)),
        "publication_rate_hz": publication_rate_hz,
        "content_rules": [
            _content_rule_from_dict(r) for r in d.get("content_rules", [])
        ],
        "require_liveliness": bool(d.get("require_liveliness", True)),
    }
    if "publication_rate_default_level" in d:
        kwargs["publication_rate_default_level"] = parse_level(
            d["publication_rate_default_level"]
        )
    return Expectation(**kwargs)


def _flatten_expectations(
    config: dict,
) -> "dict[tuple[str, str], Expectation]":
    """Walk `sources[].subjects[]` into a (source, subject) → Expectation map.

    Subject names must be unique within a source; collisions raise ValueError.
    Source names must also be unique.
    """
    out: "dict[tuple[str, str], Expectation]" = {}
    seen_sources: set[str] = set()
    for src in config.get("sources", []):
        source_name = src["name"]
        if source_name in seen_sources:
            raise ValueError(f"duplicate source name: {source_name!r}")
        seen_sources.add(source_name)
        seen_subjects: set[str] = set()
        for subj in src.get("subjects", []):
            subject_name = subj["name"]
            if subject_name in seen_subjects:
                raise ValueError(
                    f"duplicate subject {subject_name!r} under source {source_name!r}"
                )
            seen_subjects.add(subject_name)
            out[(source_name, subject_name)] = _expectation_from_dict(subj)
    return out


def _monitoring_realm_entity(
    config: dict, args: argparse.Namespace | None
) -> tuple[str, str]:
    """Realm + entity to construct *monitored* key expressions.

    Config takes precedence so an entity_health connector can watch a
    different entity than the one it publishes its own output on.
    """
    realm = config.get("realm") or (args.realm if args is not None else "")
    entity_id = config.get("entity_id") or (args.entity_id if args is not None else "")
    return realm, entity_id


def _decode_payload(key: str, raw: bytes):
    """Decode an Envelope + typed payload. Returns None on failure."""
    try:
        _received_at, _enclosed_at, payload_bytes = keelson.uncover(raw)
    except Exception:
        logger.debug("Failed to uncover envelope on %s", key, exc_info=True)
        return None
    try:
        subject = get_subject_from_pubsub_key(key)
    except Exception:
        return None
    try:
        return keelson.decode_protobuf_payload_from_type_name(
            payload_bytes, keelson.get_subject_schema(subject)
        )
    except Exception:
        logger.debug("Failed to decode payload for subject=%s", subject, exc_info=True)
        return None


def _make_handler(key: tuple[str, str]):
    def _handler(sample: zenoh.Sample):
        now = time.monotonic()
        payload = _decode_payload(str(sample.key_expr), sample.payload.to_bytes())
        with STATE_LOCK:
            ev = EVALUATORS.get(key)
            if ev is not None:
                ev.record(now, payload)

    return _handler


def _make_pubsub_liveliness_handler(source: str):
    """Handler for the entity-wide `pubsub/*/**` liveliness subscription.

    Zenoh's wildcard matching means this subscription receives BOTH the
    legacy coarse token (whose sample key has a literal `*` as its subject
    chunk) AND every concrete subject-level token in the entity. Classify
    each sample by its subject chunk: `*` → legacy coarse (counts as
    source-level presence evidence), concrete subject → subject-level
    advertisement.

    The subscription is entity-wide rather than `pubsub/*/{source}` because
    a token covers a source by *segment prefix*, not by key intersection:
    `pubsub/location_fix/mavlink` never intersects a subscription for
    source `mavlink/gps`, yet it vouches for it. Filtering is therefore
    `token_covers_source`'s job here, not the key expression's. The
    subscription is wide enough to also see source-level tokens; those do
    not parse as pubsub keys and drop out below.

    **The two tiers match by different rules, because they answer different
    questions.** A token is evidence of two separate things:

    * *Presence* — "the process behind this source is up". Covering by
      segment prefix is right: tokens die with the Zenoh session, so a parent's
      token proves the process publishing `mavlink/gps` is alive.
    * *Advertisement* — "this source publishes this subject". Here coverage is
      wrong, and quietly harmful. A producer declaring `vehicle_mode` under its
      bare `--source-id` while fanning data out under `mavlink/gps` would have
      the parent's subject credited to the child; the child's advertised set
      becomes non-empty without containing `location_fix`, and `evaluate()`
      reads that as row (c) — NOT_ADVERTISED — while 1 Hz data flows past.
      Advertisement therefore requires the token to name this **exact** source.

    A covering-but-not-exact subject token still counts toward presence, which
    is what leaves the child on row (d)'s activity-based fallback rather than
    row (a)'s UNKNOWN.
    """

    def _handler(sample: zenoh.Sample):
        sample_key = str(sample.key_expr)
        try:
            parsed = parse_pubsub_key(sample_key)
        except Exception:
            # Not a pubsub key — a source-level token caught by the same
            # wide subscription. _make_source_liveliness_handler owns it.
            return
        subject = parsed["subject"]
        token_source = parsed["source_id"]
        if not token_covers_source(token_source, source):
            return
        advertises = token_source == source
        with STATE_LOCK:
            live = SOURCE_LIVELINESS.get(source)
            if live is None:
                return
            if subject == "*":
                if sample.kind == zenoh.SampleKind.PUT:
                    live.add_source_token(sample_key)
                    logger.debug("LEGACY LIVELINESS PUT %s ← %s", source, sample_key)
                elif sample.kind == zenoh.SampleKind.DELETE:
                    live.remove_source_token(sample_key)
                    logger.debug("LEGACY LIVELINESS DELETE %s ← %s", source, sample_key)
            else:
                if sample.kind == zenoh.SampleKind.PUT:
                    live.add_source_token(sample_key)
                    if advertises:
                        live.add_subject(subject, sample_key)
                    logger.debug(
                        "SUBJECT LIVELINESS PUT %s/%s ← %s (advertises=%s)",
                        source,
                        subject,
                        sample_key,
                        advertises,
                    )
                elif sample.kind == zenoh.SampleKind.DELETE:
                    live.remove_source_token(sample_key)
                    if advertises:
                        live.remove_subject(subject, sample_key)
                    logger.debug(
                        "SUBJECT LIVELINESS DELETE %s/%s ← %s (advertises=%s)",
                        source,
                        subject,
                        sample_key,
                        advertises,
                    )

    return _handler


def _make_source_liveliness_handler(source: str):
    """Handler for the entity-wide source-level token subscription.

    Subscribed at `{entity}/*/**`, not the exact `{entity}/*/{source}`, for
    the same reason as the pubsub handler: a source-level token declared at
    `mavlink` vouches for `mavlink/gps` but their key expressions do not
    intersect. `token_covers_source` applies the segment-prefix rule that
    the key expression cannot.

    The wide subscription also sees pubsub tokens; those do not parse as
    source-level keys and drop out below, leaving them to
    _make_pubsub_liveliness_handler.
    """

    def _handler(sample: zenoh.Sample):
        sample_key = str(sample.key_expr)
        try:
            token_source = parse_source_liveliness_key(sample_key)["source_id"]
        except Exception:
            return
        if not token_covers_source(token_source, source):
            return
        with STATE_LOCK:
            live = SOURCE_LIVELINESS.get(source)
            if live is None:
                return
            if sample.kind == zenoh.SampleKind.PUT:
                live.add_source_token(sample_key)
                logger.debug("SOURCE LIVELINESS PUT %s ← %s", source, sample_key)
            elif sample.kind == zenoh.SampleKind.DELETE:
                live.remove_source_token(sample_key)
                logger.debug("SOURCE LIVELINESS DELETE %s ← %s", source, sample_key)

    return _handler


def _undeclare_source_liveliness(source: str) -> None:
    """Tear down both liveliness subscribers for `source` and drop its state.

    Caller must hold STATE_LOCK.
    """
    subs = SOURCE_LIVELINESS_SUBSCRIBERS.pop(source, None)
    if subs is not None:
        for sub in subs:
            try:
                sub.undeclare()
            except Exception:
                logger.warning(
                    "Failed to undeclare liveliness subscriber for source %s",
                    source,
                    exc_info=True,
                )
    SOURCE_LIVELINESS_KEYS.pop(source, None)
    SOURCE_LIVELINESS.pop(source, None)


def _apply_config(new_config: dict) -> None:
    """Replace the (source, subject) expectation set and their subscribers."""
    validate(new_config, JSON_SCHEMA)

    if SESSION is None:
        # Called during initial bootstrap before the session is open:
        # just stash the parsed config; subscribers are declared in run().
        CONFIG.clear()
        CONFIG.update(new_config)
        return

    realm, entity_id = _monitoring_realm_entity(new_config, ARGS)

    with STATE_LOCK:
        desired = _flatten_expectations(new_config)
        # (source, subject) → key_expr — built once so teardown and setup
        # can compare against each evaluator's current key without recomputing.
        desired_keys = {
            (source, subject): construct_pubsub_key(realm, entity_id, subject, source)
            for (source, subject) in desired
        }
        desired_sources = {source for (source, _subject) in desired}
        # source → (pubsub-wildcard key, source-level key) for the two
        # per-source liveliness subscriptions.
        # Entity-wide, not per-source. A liveliness token covers a source by
        # segment prefix, so a token declared at `mavlink` vouches for
        # `mavlink/gps` — but `pubsub/*/mavlink` and `pubsub/*/mavlink/gps`
        # do not intersect as key expressions, and neither do the source-level
        # pair. Subscribing per-source therefore silently misses every
        # sub-qualified source: MAVLink fans its output out across
        # `{source}/gps`, `{source}/imu`, ... under one process-level token,
        # and those sources all sat at UNKNOWN while reporting a live
        # publication rate. The handlers apply `token_covers_source` instead.
        pubsub_liveliness_key = f"{realm}/@v0/{entity_id}/pubsub/*/**"
        source_liveliness_key = f"{realm}/@v0/{entity_id}/*/**"
        desired_source_keys = {
            source: (pubsub_liveliness_key, source_liveliness_key)
            for source in desired_sources
        }

        # Remove subscribers that are gone or whose key_expr changed
        for key in list(SUBSCRIBERS.keys()):
            current_key_expr = SUBSCRIBERS[key].key_expr  # set below at decl time
            if key not in desired or desired_keys[key] != current_key_expr:
                try:
                    SUBSCRIBERS.pop(key).undeclare()
                except Exception:
                    logger.warning(
                        "Failed to undeclare subscriber %s", key, exc_info=True
                    )
                EVALUATORS.pop(key, None)

        # Remove per-source liveliness state for sources that disappeared or
        # whose derived keys changed (realm/entity_id changed).
        for source in list(SOURCE_LIVELINESS_SUBSCRIBERS.keys()):
            if (
                source not in desired_sources
                or SOURCE_LIVELINESS_KEYS.get(source) != desired_source_keys[source]
            ):
                _undeclare_source_liveliness(source)

        # Add per-source liveliness subscriptions for new/changed sources.
        for source in desired_sources:
            if source in SOURCE_LIVELINESS_SUBSCRIBERS:
                continue
            pubsub_key, source_key = desired_source_keys[source]
            SOURCE_LIVELINESS.setdefault(source, SourceLiveliness())
            # History=True seeds already-live tokens (source already running
            # / advertising before this connector started watching it).
            pubsub_sub = SESSION.liveliness().declare_subscriber(
                pubsub_key,
                _make_pubsub_liveliness_handler(source),
                history=True,
            )
            source_sub = SESSION.liveliness().declare_subscriber(
                source_key,
                _make_source_liveliness_handler(source),
                history=True,
            )
            SOURCE_LIVELINESS_SUBSCRIBERS[source] = (pubsub_sub, source_sub)
            SOURCE_LIVELINESS_KEYS[source] = (pubsub_key, source_key)
            logger.info(
                "Watching liveliness for source %s → %s, %s",
                source,
                pubsub_key,
                source_key,
            )

        # Add new / replaced subscribers, or update bands on existing ones
        for key, exp in desired.items():
            source, _subject = key
            if key in EVALUATORS:
                # Same (source, subject) and same key_expr (key changes were
                # handled above by tearing down). Update bands / thresholds in
                # place so the next evaluate() picks them up. Sample history
                # is preserved.
                ev = EVALUATORS[key]
                ev.expectation = exp
                ev.window_s = exp.window_s
                # Re-point at the (possibly recreated) shared liveliness state
                # so a source-key change (realm/entity_id) doesn't leave the
                # evaluator referencing a stale SourceLiveliness instance.
                ev.liveliness = SOURCE_LIVELINESS[source]
            else:
                key_expr = desired_keys[key]
                EVALUATORS[key] = Evaluator(exp, liveliness=SOURCE_LIVELINESS[source])
                sub = SESSION.declare_subscriber(key_expr, _make_handler(key))
                # Stash the key_expr on the subscriber so reconfig can compare
                # without rebuilding it from realm/entity/source/subject.
                try:
                    sub.key_expr = key_expr  # type: ignore[attr-defined]
                except Exception:
                    pass
                SUBSCRIBERS[key] = sub
                logger.info("Subscribed %s → %s", key, key_expr)

        CONFIG.clear()
        CONFIG.update(new_config)


def get_config() -> dict:
    with STATE_LOCK:
        return json.loads(json.dumps(CONFIG))  # deep copy


def set_config(new_config: dict) -> None:
    logger.info("Applying new config via RPC")
    _apply_config(new_config)


def _build_entity_health(
    overall: int, sources: list, timestamp_ns: int
) -> EntityHealth:
    msg = EntityHealth()
    msg.timestamp.FromNanoseconds(timestamp_ns)
    msg.level = overall
    msg.rate_hz = float(CONFIG.get("publish_rate_hz", 0.1))
    for src in sources:
        sh = SourceHealth()
        sh.name = src.name
        sh.level = src.level
        for s in src.subjects:
            subj = SubjectHealth()
            subj.name = s.name
            subj.level = s.level
            subj.measured_publication_rate_hz = s.measured_publication_rate_hz
            for c in s.checks:
                subj.checks.append(
                    CheckResult(name=c.name, level=c.level, detail=c.detail)
                )
            sh.subjects.append(subj)
        msg.sources.append(sh)
    return msg


def _startup_advertised_subjects_check(
    session: zenoh.Session, config: dict, args: argparse.Namespace
) -> None:
    """One-shot advisory check: warn about watched subjects that aren't
    advertised by a source that has already adopted three-tier liveliness.

    For each watched source, query the same `pubsub/*/{source}` pattern
    used for the live subscription. If the source advertises at least one
    subject-level token (i.e. it's a three-tier adopter), any *watched*
    subject missing from that set is very likely a config typo (wrong
    subject name or source_id) — log a WARNING so it's caught at startup
    instead of silently reporting NOT_ADVERTISED forever.

    Advisory only: any failure (timeout, no responders, parsing errors)
    is swallowed and logged at debug level. Must never prevent startup.
    """
    try:
        realm, entity_id = _monitoring_realm_entity(config, args)
        for src in config.get("sources", []):
            source = src["name"]
            watched = {subj["name"] for subj in src.get("subjects", [])}
            if not watched:
                continue
            try:
                key_expr = construct_pubsub_key(realm, entity_id, "*", source)
                advertised: set[str] = set()
                for reply in session.liveliness().get(key_expr):
                    try:
                        subject = get_subject_from_pubsub_key(str(reply.ok.key_expr))
                    except Exception:
                        continue
                    if subject != "*":
                        advertised.add(subject)
            except Exception:
                logger.debug(
                    "Startup liveliness sanity check failed for source %s",
                    source,
                    exc_info=True,
                )
                continue

            if not advertised:
                # Not (yet) a three-tier adopter — nothing to sanity-check.
                continue

            for subject in sorted(watched - advertised):
                logger.warning(
                    "Source %r advertises subject-level liveliness for %s "
                    "but not %r — check subject name / source_id in config",
                    source,
                    sorted(advertised),
                    subject,
                )
    except Exception:
        logger.debug("Startup liveliness sanity check failed", exc_info=True)


def run(session: zenoh.Session, args: argparse.Namespace) -> None:
    global SESSION
    SESSION = session

    # Declare subscribers for initial config
    _apply_config(dict(CONFIG))

    # Advisory-only: surface config typos (subject name / source_id) against
    # sources that already advertise subject-level liveliness. Never allowed
    # to block or fail startup.
    _startup_advertised_subjects_check(session, dict(CONFIG), args)

    # Wire up Configurable RPC
    make_configurable(
        session,
        args.realm,
        args.entity_id,
        args.source_id,
        get_config,
        set_config,
    )

    key_health = construct_pubsub_key(
        args.realm, args.entity_id, "entity_health", args.source_id
    )
    PUBLISHERS["entity_health"] = declare_publisher(session, key_health)
    logger.info("Publishing EntityHealth on %s", key_health)

    while True:
        rate = max(float(CONFIG.get("publish_rate_hz", 0.1)), 0.01)
        time.sleep(1.0 / rate)

        now = time.monotonic()
        with STATE_LOCK:
            overall, sources = evaluate_grouped(EVALUATORS, now)
        stamp = time.time_ns()
        msg = _build_entity_health(overall, sources, stamp)
        PUBLISHERS["entity_health"].put(
            enclose(msg.SerializeToString(), enclosed_at=stamp)
        )


def main() -> None:
    global ARGS
    parser = argparse.ArgumentParser(
        prog="entity_health2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__,
    )
    add_common_arguments(parser)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the JSON configuration file.",
    )
    args = parser.parse_args()
    ARGS = args

    setup_logging(level=args.log_level)

    try:
        initial = json.loads(args.config.read_text(encoding="UTF-8"))
        validate(initial, JSON_SCHEMA)
    except json.JSONDecodeError:
        logger.exception("Config file is not valid JSON")
        sys.exit(1)
    except ValidationError:
        logger.exception("Config file does not validate against schema")
        sys.exit(1)

    CONFIG.clear()
    CONFIG.update(initial)

    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    logger.info("Opening Zenoh session...")
    with zenoh.open(zconf) as session:
        # Source-level + subject-level liveliness tokens, one per subject this
        # connector publishes. Both must be listed: a three-tier consumer
        # watching an unadvertised subject sits at NOT_ADVERTISED forever while
        # the data flows past it at full rate — the exact misconfiguration
        # _startup_advertised_subjects_check() exists to warn other people about.
        # The configurable/v1 RPC interface-level token is declared inside
        # make_configurable() (via serve_rpc), so it's not repeated here.
        with declare_liveliness(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            pubsub_subjects=["entity_health"],
        ):
            try:
                run(session, args)
            except KeyboardInterrupt:
                logger.info("Shutting down on user request")
                sys.exit(0)


if __name__ == "__main__":
    main()
