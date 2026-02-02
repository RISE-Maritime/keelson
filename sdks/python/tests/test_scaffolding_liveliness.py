"""
E2E tests for liveliness scaffolding: declare_liveliness_token and LivelinessMonitor.

Requires Zenoh in peer mode. Mark all tests with @pytest.mark.e2e.
"""

import time

import pytest
import zenoh

from keelson.scaffolding.liveliness import declare_liveliness_token


@pytest.fixture
def session():
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    s = zenoh.open(conf)
    yield s
    s.close()


@pytest.fixture
def session_b():
    """Second independent session for cross-session tests."""
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    s = zenoh.open(conf)
    yield s
    s.close()


# ---------- declare_liveliness_token tests ----------


@pytest.mark.e2e
def test_declare_token_returns_handle(session):
    """declare_liveliness_token returns a non-None token handle."""
    token = declare_liveliness_token(session, "keelson", "test_entity", "gnss/0")
    assert token is not None
    token.undeclare()


@pytest.mark.e2e
def test_declared_token_is_discoverable(session):
    """A declared token should be discoverable via liveliness().get()."""
    token = declare_liveliness_token(session, "keelson", "test_entity", "gnss/0")
    time.sleep(0.5)

    replies = session.liveliness().get("keelson/@v0/test_entity/**")
    matched = [str(reply.ok.key_expr) for reply in replies]

    assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in matched
    token.undeclare()


@pytest.mark.e2e
def test_declared_token_follows_key_convention(session):
    """The token key follows the pubsub/*/source_id convention."""
    token = declare_liveliness_token(session, "keelson", "test_entity", "camera/0")
    time.sleep(0.5)

    replies = session.liveliness().get("keelson/@v0/test_entity/**")
    matched = [str(reply.ok.key_expr) for reply in replies]

    expected = "keelson/@v0/test_entity/pubsub/*/camera/0"
    assert expected in matched, f"Expected {expected} in {matched}"
    token.undeclare()


@pytest.mark.e2e
def test_undeclare_triggers_leave_event(session, session_b):
    """Undeclaring a token triggers a leave event on a subscriber in another session."""
    events = []

    def callback(sample):
        kind = sample.kind.name if hasattr(sample.kind, "name") else str(sample.kind)
        events.append((kind, str(sample.key_expr)))

    subscriber = session.liveliness().declare_subscriber(
        "keelson/@v0/test_entity/**", callback
    )
    time.sleep(0.5)

    token = declare_liveliness_token(session_b, "keelson", "test_entity", "gnss/0")
    time.sleep(1.0)

    token.undeclare()
    time.sleep(1.0)

    subscriber.undeclare()

    assert len(events) >= 2, f"Expected join+leave events, got {events}"
