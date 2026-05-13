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
from keelson import construct_pubsub_key, enclose, get_subject_from_pubsub_key
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
    declare_liveliness_token,
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
    evaluate_grouped,
    parse_level,
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

# Module-level state (cleared between tests). Subscribers, liveliness
# subscribers, and evaluators are keyed by `(source_name, subject_name)`.
PUBLISHERS: dict[str, zenoh.Publisher] = {}
SUBSCRIBERS: dict[tuple[str, str], zenoh.Subscriber] = {}
LIVELINESS_SUBSCRIBERS: dict[tuple[str, str], zenoh.Subscriber] = {}
EVALUATORS: dict[tuple[str, str], Evaluator] = {}
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


def _make_liveliness_handler(key: tuple[str, str]):
    def _handler(sample: zenoh.Sample):
        sample_key = str(sample.key_expr)
        with STATE_LOCK:
            ev = EVALUATORS.get(key)
            if ev is None:
                return
            if sample.kind == zenoh.SampleKind.PUT:
                ev.set_alive(sample_key)
                logger.debug("LIVELINESS PUT %s ← %s", key, sample_key)
            elif sample.kind == zenoh.SampleKind.DELETE:
                ev.set_dead(sample_key)
                logger.debug("LIVELINESS DELETE %s ← %s", key, sample_key)

    return _handler


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
                try:
                    if key in LIVELINESS_SUBSCRIBERS:
                        LIVELINESS_SUBSCRIBERS.pop(key).undeclare()
                except Exception:
                    logger.warning(
                        "Failed to undeclare liveliness subscriber %s",
                        key,
                        exc_info=True,
                    )
                EVALUATORS.pop(key, None)

        # Add new / replaced subscribers, or update bands on existing ones
        for key, exp in desired.items():
            if key in EVALUATORS:
                # Same (source, subject) and same key_expr (key changes were
                # handled above by tearing down). Update bands / thresholds in
                # place so the next evaluate() picks them up. Sample history
                # is preserved.
                ev = EVALUATORS[key]
                ev.expectation = exp
                ev.window_s = exp.window_s
            else:
                key_expr = desired_keys[key]
                EVALUATORS[key] = Evaluator(exp)
                sub = SESSION.declare_subscriber(key_expr, _make_handler(key))
                # Stash the key_expr on the subscriber so reconfig can compare
                # without rebuilding it from realm/entity/source/subject.
                try:
                    sub.key_expr = key_expr  # type: ignore[attr-defined]
                except Exception:
                    pass
                SUBSCRIBERS[key] = sub
                # Liveliness subscriber with history=True seeds already-live tokens.
                LIVELINESS_SUBSCRIBERS[key] = SESSION.liveliness().declare_subscriber(
                    key_expr,
                    _make_liveliness_handler(key),
                    history=True,
                )
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


def run(session: zenoh.Session, args: argparse.Namespace) -> None:
    global SESSION
    SESSION = session

    # Declare subscribers for initial config
    _apply_config(dict(CONFIG))

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
    PUBLISHERS["entity_health"] = session.declare_publisher(key_health)
    logger.info("Publishing EntityHealth on %s", key_health)

    while True:
        rate = max(float(CONFIG.get("publish_rate_hz", 0.1)), 0.01)
        time.sleep(1.0 / rate)

        now = time.monotonic()
        with STATE_LOCK:
            overall, sources = evaluate_grouped(EVALUATORS, now)
        msg = _build_entity_health(overall, sources, time.time_ns())
        PUBLISHERS["entity_health"].put(
            enclose(msg.SerializeToString(), enclosed_at=time.time_ns())
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
    )

    logger.info("Opening Zenoh session...")
    with zenoh.open(zconf) as session:
        with declare_liveliness_token(
            session, args.realm, args.entity_id, args.source_id
        ):
            try:
                run(session, args)
            except KeyboardInterrupt:
                logger.info("Shutting down on user request")
                sys.exit(0)


if __name__ == "__main__":
    main()
