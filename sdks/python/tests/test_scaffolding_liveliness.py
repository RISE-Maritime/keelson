"""
E2E tests for liveliness scaffolding: declare_liveliness_token and LivelinessMonitor.

Requires Zenoh in peer mode. Mark all tests with @pytest.mark.e2e.
"""

import time

import pytest
import zenoh

from keelson.scaffolding.liveliness import (
    LivelinessMonitor,
    declare_liveliness_token,
)


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
    with declare_liveliness_token(session, "keelson", "test_entity", "gnss/0") as token:
        assert token is not None


@pytest.mark.e2e
def test_declared_token_is_discoverable(session):
    """A declared token should be discoverable via liveliness().get()."""
    with declare_liveliness_token(session, "keelson", "test_entity", "gnss/0"):
        time.sleep(0.5)

        replies = session.liveliness().get("keelson/@v0/test_entity/**")
        matched = [str(reply.ok.key_expr) for reply in replies]

        assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in matched


@pytest.mark.e2e
def test_declared_token_follows_key_convention(session):
    """The token key follows the pubsub/*/source_id convention."""
    with declare_liveliness_token(session, "keelson", "test_entity", "camera/0"):
        time.sleep(0.5)

        replies = session.liveliness().get("keelson/@v0/test_entity/**")
        matched = [str(reply.ok.key_expr) for reply in replies]

        expected = "keelson/@v0/test_entity/pubsub/*/camera/0"
        assert expected in matched, f"Expected {expected} in {matched}"


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

    with declare_liveliness_token(session_b, "keelson", "test_entity", "gnss/0"):
        time.sleep(1.0)

    # Token undeclared by context manager exit
    time.sleep(1.0)

    subscriber.undeclare()

    assert len(events) >= 2, f"Expected join+leave events, got {events}"


@pytest.mark.e2e
def test_token_context_manager_undeclares_on_exit(session, session_b):
    """Exiting a token context manager triggers a leave event."""
    leaves = []

    with LivelinessMonitor(
        session,
        "keelson/@v0/test_entity/**",
        on_leave=lambda k: leaves.append(k),
    ) as monitor:
        time.sleep(0.5)

        with declare_liveliness_token(session_b, "keelson", "test_entity", "gnss/0"):
            time.sleep(1.0)
            assert monitor.count() >= 1

        # Token undeclared by context manager exit
        time.sleep(1.0)
        assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in leaves


# ---------- LivelinessMonitor tests ----------


@pytest.mark.e2e
def test_monitor_detects_join(session, session_b):
    """Monitor detects a join event from another session."""
    joins = []

    with LivelinessMonitor(
        session,
        "keelson/@v0/test_entity/**",
        on_join=lambda k: joins.append(k),
    ) as monitor:
        time.sleep(0.5)
        with declare_liveliness_token(
            session_b, "keelson", "test_entity", "gnss/0"
        ):
            time.sleep(1.0)

            assert monitor.count() >= 1
            assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in joins


@pytest.mark.e2e
def test_monitor_detects_leave(session, session_b):
    """Monitor detects a leave event when a token is undeclared."""
    leaves = []

    with LivelinessMonitor(
        session,
        "keelson/@v0/test_entity/**",
        on_leave=lambda k: leaves.append(k),
    ) as monitor:
        time.sleep(0.5)
        with declare_liveliness_token(
            session_b, "keelson", "test_entity", "gnss/0"
        ):
            time.sleep(1.0)

        # Token undeclared by context manager exit
        time.sleep(1.0)

        assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in leaves
        assert monitor.count() == 0


@pytest.mark.e2e
def test_get_alive_tracks_multiple_sources(session, session_b):
    """get_alive() tracks multiple sources and updates on leave."""
    with LivelinessMonitor(session, "keelson/@v0/test_entity/**") as monitor:
        time.sleep(0.5)

        with declare_liveliness_token(
            session_b, "keelson", "test_entity", "gnss/0"
        ):
            with declare_liveliness_token(
                session_b, "keelson", "test_entity", "camera/0"
            ):
                time.sleep(1.0)

                alive = monitor.get_alive()
                assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in alive
                assert "keelson/@v0/test_entity/pubsub/*/camera/0" in alive
                assert monitor.count() == 2

            # camera/0 token undeclared
            time.sleep(1.0)

            alive = monitor.get_alive()
            assert "keelson/@v0/test_entity/pubsub/*/camera/0" not in alive
            assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in alive
            assert monitor.count() == 1


@pytest.mark.e2e
def test_is_alive_returns_correctly(session, session_b):
    """is_alive() returns True for live tokens and False otherwise."""
    key = "keelson/@v0/test_entity/pubsub/*/gnss/0"

    with LivelinessMonitor(session, "keelson/@v0/test_entity/**") as monitor:
        time.sleep(0.5)
        assert monitor.is_alive(key) is False

        with declare_liveliness_token(
            session_b, "keelson", "test_entity", "gnss/0"
        ):
            time.sleep(1.0)
            assert monitor.is_alive(key) is True

        # Token undeclared by context manager exit
        time.sleep(1.0)
        assert monitor.is_alive(key) is False


@pytest.mark.e2e
def test_context_manager(session):
    """LivelinessMonitor supports context manager protocol."""
    monitor = LivelinessMonitor(session, "keelson/@v0/test_entity/**")
    with monitor:
        assert monitor.count() == 0
    # After exit, subscriber is closed (no error on close)
    assert monitor._subscriber is None


@pytest.mark.e2e
def test_history_picks_up_existing_tokens(session, session_b):
    """With history=True (default), monitor picks up pre-existing tokens."""
    with declare_liveliness_token(session_b, "keelson", "test_entity", "gnss/0"):
        time.sleep(0.5)

        with LivelinessMonitor(
            session, "keelson/@v0/test_entity/**", history=True
        ) as monitor:
            time.sleep(1.0)
            assert monitor.is_alive("keelson/@v0/test_entity/pubsub/*/gnss/0")


@pytest.mark.e2e
def test_callback_exception_does_not_crash_monitor(session, session_b):
    """An exception in a callback does not crash the monitor."""
    good_joins = []

    def bad_on_join(key):
        raise RuntimeError("intentional error")

    def good_on_leave(key):
        good_joins.append(key)

    with LivelinessMonitor(
        session,
        "keelson/@v0/test_entity/**",
        on_join=bad_on_join,
        on_leave=good_on_leave,
    ) as monitor:
        time.sleep(0.5)

        with declare_liveliness_token(
            session_b, "keelson", "test_entity", "gnss/0"
        ):
            time.sleep(1.0)

            # Join callback raised, but monitor still tracks the token
            assert monitor.count() >= 1

        # Token undeclared by context manager exit
        time.sleep(1.0)

        # Leave callback still works despite earlier join error
        assert "keelson/@v0/test_entity/pubsub/*/gnss/0" in good_joins
