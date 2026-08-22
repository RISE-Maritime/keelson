"""In-process test of set_config() without Zenoh.

Exercises the reconfiguration code path by faking the Zenoh session.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from keelson import construct_pubsub_key, construct_source_liveliness_key


def test_set_config_without_session_updates_config(entity_health_module):
    mod = entity_health_module
    new = {
        "publish_rate_hz": 2.0,
        "sources": [
            {
                "name": "dev1",
                "subjects": [{"name": "a", "inactive_after_s": 5.0}],
            }
        ],
    }
    mod.set_config(new)
    assert mod.get_config()["publish_rate_hz"] == 2.0


def test_set_config_with_session_declares_subscribers(entity_health_module):
    mod = entity_health_module

    subs: list[MagicMock] = []
    liveliness_subs: list[MagicMock] = []

    def _declare_subscriber(key_expr, handler):
        sub = MagicMock()
        sub.key_expr = key_expr
        subs.append(sub)
        return sub

    def _declare_liveliness_subscriber(key_expr, handler, history=False):
        sub = MagicMock()
        sub.key_expr = key_expr
        liveliness_subs.append(sub)
        return sub

    session = MagicMock()
    session.declare_subscriber.side_effect = _declare_subscriber
    session.liveliness().declare_subscriber.side_effect = _declare_liveliness_subscriber
    mod.SESSION = session
    mod.ARGS = SimpleNamespace(realm="r", entity_id="e", source_id="health")

    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "sources": [
                {
                    "name": "dev1",
                    "subjects": [
                        {"name": "a", "inactive_after_s": 5.0},
                        {"name": "b", "inactive_after_s": 5.0},
                    ],
                },
                {
                    "name": "dev2",
                    "subjects": [{"name": "a", "inactive_after_s": 5.0}],
                },
            ],
        }
    )
    assert set(mod.SUBSCRIBERS.keys()) == {("dev1", "a"), ("dev1", "b"), ("dev2", "a")}
    assert set(mod.EVALUATORS.keys()) == {("dev1", "a"), ("dev1", "b"), ("dev2", "a")}
    # key_expr should be exactly what construct_pubsub_key produces — pin the
    # contract, not a substring of it.
    assert mod.SUBSCRIBERS[("dev1", "a")].key_expr == construct_pubsub_key(
        "r", "e", "a", "dev1"
    )
    assert mod.SUBSCRIBERS[("dev1", "b")].key_expr == construct_pubsub_key(
        "r", "e", "b", "dev1"
    )
    assert mod.SUBSCRIBERS[("dev2", "a")].key_expr == construct_pubsub_key(
        "r", "e", "a", "dev2"
    )

    # Reconfigure: drop dev1/b, keep the others, add dev2/c
    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "sources": [
                {
                    "name": "dev1",
                    "subjects": [{"name": "a", "inactive_after_s": 5.0}],
                },
                {
                    "name": "dev2",
                    "subjects": [
                        {"name": "a", "inactive_after_s": 5.0},
                        {"name": "c", "inactive_after_s": 5.0},
                    ],
                },
            ],
        }
    )
    assert set(mod.SUBSCRIBERS.keys()) == {("dev1", "a"), ("dev2", "a"), ("dev2", "c")}
    assert ("dev1", "b") not in mod.EVALUATORS


def test_set_config_uses_realm_entity_from_config_when_present(entity_health_module):
    """Config-supplied realm/entity_id should override CLI args for monitored keys."""
    mod = entity_health_module

    captured: list[str] = []

    def _declare_subscriber(key_expr, handler):
        captured.append(key_expr)
        sub = MagicMock()
        sub.key_expr = key_expr
        return sub

    session = MagicMock()
    session.declare_subscriber.side_effect = _declare_subscriber
    session.liveliness().declare_subscriber.return_value = MagicMock()
    mod.SESSION = session
    mod.ARGS = SimpleNamespace(
        realm="cli_realm", entity_id="cli_entity", source_id="health"
    )

    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "realm": "cfg_realm",
            "entity_id": "cfg_entity",
            "sources": [
                {"name": "dev1", "subjects": [{"name": "a"}]},
            ],
        }
    )
    # Every captured key must use the config-supplied realm/entity, not just one.
    assert captured, "expected at least one subscriber declaration"
    assert all("cfg_realm" in k and "cfg_entity" in k for k in captured)
    assert not any("cli_realm" in k for k in captured)


def test_set_config_falls_back_to_cli_realm_entity(entity_health_module):
    mod = entity_health_module

    captured: list[str] = []

    def _declare_subscriber(key_expr, handler):
        captured.append(key_expr)
        sub = MagicMock()
        sub.key_expr = key_expr
        return sub

    session = MagicMock()
    session.declare_subscriber.side_effect = _declare_subscriber
    session.liveliness().declare_subscriber.return_value = MagicMock()
    mod.SESSION = session
    mod.ARGS = SimpleNamespace(
        realm="cli_realm", entity_id="cli_entity", source_id="health"
    )

    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "sources": [{"name": "dev1", "subjects": [{"name": "a"}]}],
        }
    )
    assert all("cli_realm" in k and "cli_entity" in k for k in captured)


def _fake_session(liveliness_subs: list[MagicMock]):
    """Session stub good enough for _apply_config's declare calls.

    Every liveliness subscriber it hands out is appended to `liveliness_subs`
    so a test can count declarations and check what was undeclared.
    """
    session = MagicMock()
    session.declare_subscriber.side_effect = lambda key_expr, handler: MagicMock(
        key_expr=key_expr
    )

    def _declare_liveliness(key_expr, handler, history=False):
        sub = MagicMock(key_expr=key_expr, handler=handler, history=history)
        liveliness_subs.append(sub)
        return sub

    session.liveliness().declare_subscriber.side_effect = _declare_liveliness
    return session


def _sources(*names: str) -> dict:
    return {
        "publish_rate_hz": 1.0,
        "sources": [
            {"name": n, "subjects": [{"name": "a", "inactive_after_s": 5.0}]}
            for n in names
        ],
    }


def test_liveliness_subscribers_are_one_shared_pair(entity_health_module):
    """N sources → 2 subscribers, not 2N.

    Both keys are entity-wide and identical for every source, so per-source
    copies only multiplied delivery of every token event by N.
    """
    mod = entity_health_module
    declared: list[MagicMock] = []
    mod.SESSION = _fake_session(declared)
    mod.ARGS = SimpleNamespace(realm="r", entity_id="e", source_id="health")

    mod.set_config(_sources("dev1", "dev2", "dev3"))

    assert len(declared) == 2
    assert mod.LIVELINESS_SUBSCRIBERS == (declared[0], declared[1])
    assert set(mod.SOURCE_LIVELINESS) == {"dev1", "dev2", "dev3"}
    # Pinned to the SDK builders, not the literal layout.
    assert declared[0].key_expr == construct_pubsub_key("r", "e", "*", "**")
    assert declared[1].key_expr == construct_source_liveliness_key("r", "e", "**")
    assert declared[0].handler is mod._pubsub_liveliness_handler
    assert declared[1].handler is mod._source_liveliness_handler
    assert all(s.history for s in declared)


def test_adding_a_source_redeclares_the_pair_for_history_replay(
    entity_health_module,
):
    """history=True only replays live tokens at declaration time.

    A source joining an existing subscription would otherwise never hear about
    a producer that was already up, and sit at UNKNOWN until it restarted.
    """
    mod = entity_health_module
    declared: list[MagicMock] = []
    mod.SESSION = _fake_session(declared)
    mod.ARGS = SimpleNamespace(realm="r", entity_id="e", source_id="health")

    mod.set_config(_sources("dev1"))
    first_pair = declared[:]
    mod.set_config(_sources("dev1", "dev2"))

    assert len(declared) == 4
    for sub in first_pair:
        sub.undeclare.assert_called_once()
    assert mod.LIVELINESS_SUBSCRIBERS == (declared[2], declared[3])
    assert set(mod.SOURCE_LIVELINESS) == {"dev1", "dev2"}


def test_removing_a_source_or_changing_bands_keeps_the_pair(entity_health_module):
    """Nothing about the subscription depends on the source set shrinking."""
    mod = entity_health_module
    declared: list[MagicMock] = []
    mod.SESSION = _fake_session(declared)
    mod.ARGS = SimpleNamespace(realm="r", entity_id="e", source_id="health")

    mod.set_config(_sources("dev1", "dev2"))
    pair = mod.LIVELINESS_SUBSCRIBERS

    # Drop dev2 ...
    mod.set_config(_sources("dev1"))
    assert mod.LIVELINESS_SUBSCRIBERS is pair
    assert set(mod.SOURCE_LIVELINESS) == {"dev1"}
    # ... and tweak dev1's thresholds in place.
    cfg = _sources("dev1")
    cfg["sources"][0]["subjects"][0]["inactive_after_s"] = 9.0
    mod.set_config(cfg)
    assert mod.LIVELINESS_SUBSCRIBERS is pair
    assert len(declared) == 2
    for sub in declared:
        sub.undeclare.assert_not_called()


def test_identity_change_redeclares_on_the_new_keys(entity_health_module):
    mod = entity_health_module
    declared: list[MagicMock] = []
    mod.SESSION = _fake_session(declared)
    mod.ARGS = SimpleNamespace(realm="r", entity_id="vessel-a", source_id="health")

    mod.set_config(_sources("dev1"))
    old_state = mod.SOURCE_LIVELINESS["dev1"]
    old_state.add_source_token("r/@v0/vessel-a/*/dev1")

    mod.set_config({**_sources("dev1"), "realm": "r", "entity_id": "vessel-b"})

    assert mod.LIVELINESS_KEYS == (
        construct_pubsub_key("r", "vessel-b", "*", "**"),
        construct_source_liveliness_key("r", "vessel-b", "**"),
    )
    for sub in declared[:2]:
        sub.undeclare.assert_called_once()
    # vessel-a's tokens must not vouch for vessel-b's sources.
    assert mod.SOURCE_LIVELINESS["dev1"] is not old_state
    assert not mod.SOURCE_LIVELINESS["dev1"].is_present
    assert mod.EVALUATORS[("dev1", "a")].liveliness is mod.SOURCE_LIVELINESS["dev1"]


def test_shared_handler_fans_one_token_out_to_every_covered_source(
    entity_health_module,
):
    mod = entity_health_module
    declared: list[MagicMock] = []
    mod.SESSION = _fake_session(declared)
    mod.ARGS = SimpleNamespace(realm="r", entity_id="e", source_id="health")
    mod.set_config(_sources("mavlink", "mavlink/gps", "labjack"))

    sample = SimpleNamespace(
        key_expr="r/@v0/e/*/mavlink", kind=mod.zenoh.SampleKind.PUT
    )
    mod._source_liveliness_handler(sample)

    assert mod.SOURCE_LIVELINESS["mavlink"].is_present
    assert mod.SOURCE_LIVELINESS["mavlink/gps"].is_present
    assert not mod.SOURCE_LIVELINESS["labjack"].is_present
