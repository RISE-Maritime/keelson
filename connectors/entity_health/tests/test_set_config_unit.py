"""In-process test of set_config() without Zenoh.

Exercises the reconfiguration code path by faking the Zenoh session.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from keelson import construct_pubsub_key


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


def _fake_session():
    """Session stub good enough for _apply_config's declare calls."""
    session = MagicMock()
    session.declare_subscriber.side_effect = lambda key_expr, handler: MagicMock(
        key_expr=key_expr
    )
    session.liveliness().declare_subscriber.side_effect = (
        lambda key_expr, handler, history=False: MagicMock(key_expr=key_expr)
    )
    return session


_ONE_SOURCE = {
    "publish_rate_hz": 1.0,
    "sources": [{"name": "dev1", "subjects": [{"name": "a", "inactive_after_s": 5.0}]}],
}


def test_reconfig_to_another_vessel_resets_authority_hysteresis(entity_health_module):
    """Hysteresis is a claim about *this* vessel's recent history.

    A reconfig that swaps realm/entity_id is monitoring a different vessel, so
    carrying the previous level across would let one vessel's earned autonomy
    license another's climb without the margin the sticky ladder demands.
    """
    mod = entity_health_module
    mod.SESSION = _fake_session()
    mod.ARGS = SimpleNamespace(realm="r", entity_id="vessel-a", source_id="health")

    mod.set_config(_ONE_SOURCE)
    assert mod.MONITORED_IDENTITY == ("r", "vessel-a")

    # A tick has run and earned a level.
    mod.PREVIOUS_AUTHORITY_LEVEL = 4

    mod.set_config({**_ONE_SOURCE, "realm": "r", "entity_id": "vessel-b"})

    assert mod.MONITORED_IDENTITY == ("r", "vessel-b")
    assert (
        mod.PREVIOUS_AUTHORITY_LEVEL is None
    ), "vessel-a's hysteresis must not carry into determinations for vessel-b"


def test_reconfig_within_one_vessel_keeps_hysteresis(entity_health_module):
    """Adding a sensor is not a reason to license an unmargined climb.

    Clearing the previous level restores the bare ladder, which can climb
    without the margin `level_for()` demands — the direction it deliberately
    puts the burden of proof on. Only an identity change justifies that.
    """
    mod = entity_health_module
    mod.SESSION = _fake_session()
    mod.ARGS = SimpleNamespace(realm="r", entity_id="vessel-a", source_id="health")

    mod.set_config(_ONE_SOURCE)
    mod.PREVIOUS_AUTHORITY_LEVEL = 4

    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "sources": [
                {"name": "dev1", "subjects": [{"name": "a", "inactive_after_s": 5.0}]},
                {"name": "dev2", "subjects": [{"name": "a", "inactive_after_s": 5.0}]},
            ],
        }
    )

    assert mod.PREVIOUS_AUTHORITY_LEVEL == 4
