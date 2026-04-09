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
    EntityHealth,
    SubsystemHealth,
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
    evaluate_all,
    parse_level,
)

logger = logging.getLogger("entity_health")

JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "title": "Entity Health Config",
    "properties": {
        "publish_rate_hz": {"type": "number", "exclusiveMinimum": 0},
        "expectations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "key_expr"],
                "properties": {
                    "name": {"type": "string"},
                    "key_expr": {"type": "string"},
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
            },
        },
    },
    "required": ["expectations"],
    "additionalProperties": False,
}

# Module-level state (cleared between tests)
PUBLISHERS: dict[str, zenoh.Publisher] = {}
SUBSCRIBERS: dict[str, zenoh.Subscriber] = {}
LIVELINESS_SUBSCRIBERS: dict[str, zenoh.Subscriber] = {}
EVALUATORS: dict[str, Evaluator] = {}
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
        "key_expr": d["key_expr"],
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


def _make_handler(name: str):
    def _handler(sample: zenoh.Sample):
        now = time.monotonic()
        payload = _decode_payload(str(sample.key_expr), sample.payload.to_bytes())
        with STATE_LOCK:
            ev = EVALUATORS.get(name)
            if ev is not None:
                ev.record(now, payload)

    return _handler


def _make_liveliness_handler(name: str, own_source_id: str):
    own_suffix = f"/{own_source_id}"

    def _handler(sample: zenoh.Sample):
        key = str(sample.key_expr)
        # Liveliness subscribers match by intersection across all source_ids
        # under the entity, so we receive our own token too. Ignore it —
        # otherwise our own presence would mask the absence of the actual
        # data publisher.
        if key.endswith(own_suffix):
            return
        with STATE_LOCK:
            ev = EVALUATORS.get(name)
            if ev is None:
                return
            if sample.kind == zenoh.SampleKind.PUT:
                ev.set_alive(key)
                logger.debug("LIVELINESS PUT %s ← %s", name, key)
            elif sample.kind == zenoh.SampleKind.DELETE:
                ev.set_dead(key)
                logger.debug("LIVELINESS DELETE %s ← %s", name, key)

    return _handler


def _apply_config(new_config: dict) -> None:
    """Replace expectations (and their subscribers) with a new set."""
    validate(new_config, JSON_SCHEMA)

    if SESSION is None:
        # Called during initial bootstrap before the session is open:
        # just stash the parsed config; subscribers are declared in run().
        CONFIG.clear()
        CONFIG.update(new_config)
        return

    with STATE_LOCK:
        desired = {
            e["name"]: _expectation_from_dict(e) for e in new_config["expectations"]
        }

        # Remove subscribers that are gone or whose key_expr changed
        for name in list(SUBSCRIBERS.keys()):
            if (
                name not in desired
                or desired[name].key_expr != EVALUATORS[name].expectation.key_expr
            ):
                try:
                    SUBSCRIBERS.pop(name).undeclare()
                except Exception:
                    logger.warning(
                        "Failed to undeclare subscriber %s", name, exc_info=True
                    )
                try:
                    if name in LIVELINESS_SUBSCRIBERS:
                        LIVELINESS_SUBSCRIBERS.pop(name).undeclare()
                except Exception:
                    logger.warning(
                        "Failed to undeclare liveliness subscriber %s",
                        name,
                        exc_info=True,
                    )
                EVALUATORS.pop(name, None)

        # Add new / replaced subscribers, or update bands on existing ones
        for name, exp in desired.items():
            if name in EVALUATORS:
                # Same name + same key_expr (key_expr changes were handled above
                # by tearing down). Update bands / thresholds in place so the
                # next evaluate() picks them up. Sample history is preserved.
                ev = EVALUATORS[name]
                ev.expectation = exp
                ev.window_s = exp.window_s
            else:
                EVALUATORS[name] = Evaluator(exp)
                SUBSCRIBERS[name] = SESSION.declare_subscriber(
                    exp.key_expr, _make_handler(name)
                )
                # Liveliness subscriber with history=True seeds already-live tokens.
                # Pass our own source_id so the handler can filter our own token.
                own_source_id = ARGS.source_id if ARGS is not None else ""
                LIVELINESS_SUBSCRIBERS[name] = SESSION.liveliness().declare_subscriber(
                    exp.key_expr,
                    _make_liveliness_handler(name, own_source_id),
                    history=True,
                )
                logger.info("Subscribed %s → %s", name, exp.key_expr)

        CONFIG.clear()
        CONFIG.update(new_config)


def get_config() -> dict:
    with STATE_LOCK:
        return json.loads(json.dumps(CONFIG))  # deep copy


def set_config(new_config: dict) -> None:
    logger.info("Applying new config via RPC")
    _apply_config(new_config)


def _build_entity_health(
    overall: int, subsystems: list, timestamp_ns: int
) -> EntityHealth:
    msg = EntityHealth()
    msg.timestamp.FromNanoseconds(timestamp_ns)
    msg.level = overall
    msg.rate_hz = float(CONFIG.get("publish_rate_hz", 0.1))
    for s in subsystems:
        sh = SubsystemHealth()
        sh.name = s.name
        sh.level = s.level
        sh.detail = s.detail
        msg.subsystems.append(sh)
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
            overall, states = evaluate_all(EVALUATORS.values(), now)
        msg = _build_entity_health(overall, states, time.time_ns())
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
