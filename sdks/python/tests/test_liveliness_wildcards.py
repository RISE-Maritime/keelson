"""
Tests verifying Zenoh liveliness token behavior for keelson health monitoring.

These tests validate:
- declare_token() with concrete keys
- declare_token() with wildcard (*) in the key
- declare_subscriber() with ** wildcard receives join/leave events
- liveliness().get() with ** returns matching live tokens
- Verbatim chunk (@v0) isolation guarantees
"""

import time

import pytest
import zenoh


@pytest.fixture
def session():
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    s = zenoh.open(conf)
    yield s
    s.close()


@pytest.fixture
def session_b():
    """A second independent session for cross-session token tests."""
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    s = zenoh.open(conf)
    yield s
    s.close()


@pytest.mark.e2e
def test_concrete_token_declare_and_get(session):
    """A token declared with a concrete key should be retrievable via liveliness().get()."""
    key = "keelson/@v0/test_entity/pubsub/sensor_status/gnss/0"
    token = session.liveliness().declare_token(key)
    time.sleep(0.5)

    replies = session.liveliness().get("keelson/@v0/test_entity/**")
    matched = [str(reply.ok.key_expr) for reply in replies]

    assert key in matched, f"Expected {key} in {matched}"
    token.undeclare()


@pytest.mark.e2e
def test_wildcard_token_matches_concrete_query(session):
    """
    Test whether a token declared with * acts as a pattern matching concrete queries.

    The RFC proposes: declare a token on
        keelson/@v0/entity_a/pubsub/*/gnss/0
    and query with a concrete subject like:
        keelson/@v0/entity_a/pubsub/location_fix/gnss/0

    This test documents whether the wildcard token is returned by such a
    concrete query (true pattern matching) or whether * is treated as a
    literal character.
    """
    wildcard_key = "keelson/@v0/entity_a/pubsub/*/gnss/0"
    concrete_query = "keelson/@v0/entity_a/pubsub/location_fix/gnss/0"

    token = None
    try:
        token = session.liveliness().declare_token(wildcard_key)
        time.sleep(0.5)

        # Query with a concrete key that would match if * is a real wildcard
        replies = session.liveliness().get(concrete_query)
        matched = [str(reply.ok.key_expr) for reply in replies]

        if wildcard_key in matched or concrete_query in matched:
            pytest.skip(
                "Wildcard (*) in token key DOES act as a pattern: "
                f"concrete query returned {matched}. "
                "RFC Option A (pubsub/*/source_id) is viable."
            )
        else:
            # Also check if a broad query returns it as a literal
            broad_replies = session.liveliness().get("keelson/@v0/entity_a/**")
            broad_matched = [str(r.ok.key_expr) for r in broad_replies]

            pytest.skip(
                "Wildcard (*) in token key does NOT match concrete queries. "
                f"Concrete query returned: {matched}. "
                f"Broad ** query returned: {broad_matched}. "
                "RFC Option B (@alive/source_id with concrete keys) is needed."
            )
    except Exception as e:
        pytest.skip(
            f"Wildcard (*) in token key is NOT supported. "
            f"Exception: {type(e).__name__}: {e}"
        )
    finally:
        if token is not None:
            try:
                token.undeclare()
            except Exception:
                pass


@pytest.mark.e2e
def test_subscriber_wildcard_receives_join_leave(session, session_b):
    """
    A liveliness subscriber with ** wildcard should receive join/leave events
    from concrete tokens declared in another session.
    """
    events = []

    def callback(sample):
        events.append(
            (
                sample.kind.name if hasattr(sample.kind, "name") else str(sample.kind),
                str(sample.key_expr),
            )
        )

    subscriber = session.liveliness().declare_subscriber(
        "keelson/@v0/test_entity/**",
        callback,
    )
    time.sleep(0.5)

    # Declare token in session_b so session sees a join
    token = session_b.liveliness().declare_token(
        "keelson/@v0/test_entity/pubsub/sensor_status/camera/0"
    )
    time.sleep(1.0)

    # Undeclare to trigger leave
    token.undeclare()
    time.sleep(1.0)

    subscriber.undeclare()

    # Zenoh may report as PUT/DELETE or similar depending on version
    assert (
        len(events) >= 2
    ), f"Expected at least 2 events (join+leave), got {len(events)}: {events}"


@pytest.mark.e2e
def test_get_wildcard_returns_matching_tokens(session):
    """liveliness().get() with ** should return all matching live tokens."""
    tokens = []
    keys = [
        "keelson/@v0/entity_a/pubsub/sensor_status/gnss/0",
        "keelson/@v0/entity_a/pubsub/sensor_status/camera/0",
        "keelson/@v0/entity_a/pubsub/location_fix/gnss/0",
    ]

    for key in keys:
        tokens.append(session.liveliness().declare_token(key))
    time.sleep(0.5)

    replies = session.liveliness().get("keelson/@v0/entity_a/**")
    matched = sorted([str(reply.ok.key_expr) for reply in replies])

    for key in keys:
        assert key in matched, f"Expected {key} in {matched}"

    for token in tokens:
        token.undeclare()


@pytest.mark.e2e
def test_verbatim_chunk_isolation(session):
    """
    Verbatim chunks (@v0, @v1) provide key-space isolation.
    A subscriber on @v0/** must NOT see tokens declared under @v1/**.
    """
    v0_key = "keelson/@v0/test_entity/pubsub/sensor_status/gnss/0"
    v1_key = "keelson/@v1/test_entity/pubsub/sensor_status/gnss/0"

    token_v0 = session.liveliness().declare_token(v0_key)
    token_v1 = session.liveliness().declare_token(v1_key)
    time.sleep(0.5)

    # Query only @v0
    replies_v0 = session.liveliness().get("keelson/@v0/**")
    matched_v0 = [str(reply.ok.key_expr) for reply in replies_v0]

    # Query only @v1
    replies_v1 = session.liveliness().get("keelson/@v1/**")
    matched_v1 = [str(reply.ok.key_expr) for reply in replies_v1]

    assert v0_key in matched_v0, f"Expected {v0_key} in v0 results: {matched_v0}"
    assert (
        v1_key not in matched_v0
    ), f"v1 key should NOT appear in v0 results: {matched_v0}"

    assert v1_key in matched_v1, f"Expected {v1_key} in v1 results: {matched_v1}"
    assert (
        v0_key not in matched_v1
    ), f"v0 key should NOT appear in v1 results: {matched_v1}"

    token_v0.undeclare()
    token_v1.undeclare()
