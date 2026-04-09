"""In-process test of set_config() without Zenoh.

Exercises the reconfiguration code path by faking the Zenoh session.
"""

from unittest.mock import MagicMock


def test_set_config_without_session_updates_config(entity_health_module):
    mod = entity_health_module
    new = {
        "publish_rate_hz": 2.0,
        "expectations": [{"name": "a", "key_expr": "r/**", "inactive_after_s": 5.0}],
    }
    mod.set_config(new)
    assert mod.get_config()["publish_rate_hz"] == 2.0


def test_set_config_with_session_declares_subscribers(entity_health_module):
    mod = entity_health_module

    subs: list[MagicMock] = []

    def _declare_subscriber(key_expr, handler):
        sub = MagicMock()
        sub.key_expr = key_expr
        subs.append(sub)
        return sub

    session = MagicMock()
    session.declare_subscriber.side_effect = _declare_subscriber
    mod.SESSION = session

    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "expectations": [
                {"name": "a", "key_expr": "r/a/**", "inactive_after_s": 5.0},
                {"name": "b", "key_expr": "r/b/**", "inactive_after_s": 5.0},
            ],
        }
    )
    assert set(mod.SUBSCRIBERS.keys()) == {"a", "b"}
    assert set(mod.EVALUATORS.keys()) == {"a", "b"}
    assert len(subs) == 2

    # Reconfigure: drop "b", keep "a", add "c"
    mod.set_config(
        {
            "publish_rate_hz": 1.0,
            "expectations": [
                {"name": "a", "key_expr": "r/a/**", "inactive_after_s": 5.0},
                {"name": "c", "key_expr": "r/c/**", "inactive_after_s": 5.0},
            ],
        }
    )
    assert set(mod.SUBSCRIBERS.keys()) == {"a", "c"}
    assert "b" not in mod.EVALUATORS
