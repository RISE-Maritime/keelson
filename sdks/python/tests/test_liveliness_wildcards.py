"""
Tests verifying Zenoh liveliness token behavior for keelson health monitoring.

These tests validate:
- declare_token() with concrete keys
- declare_token() with wildcard (*) in the key
- declare_subscriber() with ** wildcard receives join/leave events
- liveliness().get() with ** returns matching live tokens
- Verbatim chunk (@v0) isolation guarantees
- Verbatim chunk (@target) isolation for the target-scoped token (#253)
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


@pytest.mark.e2e
def test_target_scoped_token_is_invisible_to_every_existing_query(session):
    """The compatibility claim behind #253, against real sessions.

    `@target` is verbatim, so the five discovery patterns in §5.5 that predate
    the target-scoped token cannot reach it. That is what made the tier safe to
    add: an aggregator written before it sees exactly what it saw before.

    Written as "the existing queries return the same set with and without the
    new token" rather than "the new key is absent", because the first is the
    property operators actually depend on.
    """
    entity = "shore_station"
    plain = f"keelson/@v0/{entity}/pubsub/location_fix/ais"
    targeted = f"{plain}/@target/*"

    existing_patterns = [
        "keelson/@v0/*/*/**",  # all live producers
        f"keelson/@v0/{entity}/*/**",  # producers on this entity
        "keelson/@v0/*/pubsub/*/**",  # all advertised subjects
        "keelson/@v0/*/pubsub/location_fix/**",  # sources advertising a subject
        f"keelson/@v0/{entity}/pubsub/*/ais",  # subjects by this source
    ]

    def seen(pattern):
        return sorted(str(r.ok.key_expr) for r in session.liveliness().get(pattern))

    token_plain = session.liveliness().declare_token(plain)
    time.sleep(0.5)
    before = {p: seen(p) for p in existing_patterns}
    assert any(plain in v for v in before.values()), before

    token_targeted = session.liveliness().declare_token(targeted)
    time.sleep(0.5)
    after = {p: seen(p) for p in existing_patterns}

    try:
        assert after == before, "an existing discovery query changed its answer"
    finally:
        token_plain.undeclare()
        token_targeted.undeclare()


@pytest.mark.e2e
def test_the_target_query_finds_target_producers_and_nothing_else(session):
    """The other half: the new pattern answers the question, and answers only
    it. A source publishing about itself must not turn up in a list of sources
    publishing about others — that distinction is the whole point of the tier,
    and it is the same isolation property §2.1.1 calls load-bearing."""
    own_ship = "keelson/@v0/boat/pubsub/location_fix/gnss/0"
    ais_plain = "keelson/@v0/shore_station/pubsub/location_fix/ais"
    ais_targeted = f"{ais_plain}/@target/*"

    tokens = [
        session.liveliness().declare_token(k)
        for k in (own_ship, ais_plain, ais_targeted)
    ]
    time.sleep(0.5)

    try:
        replies = session.liveliness().get("keelson/@v0/*/pubsub/*/**/@target/**")
        matched = sorted(str(r.ok.key_expr) for r in replies)

        assert ais_targeted in matched, matched
        assert own_ship not in matched, "an own-ship source answered a target query"
        assert ais_plain not in matched, matched
    finally:
        for token in tokens:
            token.undeclare()


@pytest.mark.e2e
def test_target_token_join_and_leave_are_observable(session, session_b):
    """A target producer coming and going must be as visible as any other, or
    the tier states presence it cannot retract."""
    key = "keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/*"
    events = []
    sub = session.liveliness().declare_subscriber(
        "keelson/@v0/**/@target/**",
        lambda sample: events.append((sample.kind, str(sample.key_expr))),
    )
    time.sleep(0.3)

    token = session_b.liveliness().declare_token(key)
    time.sleep(0.5)
    token.undeclare()
    time.sleep(0.5)
    sub.undeclare()

    kinds = [kind for kind, k in events if k == key]
    assert len(kinds) >= 2, f"expected a join and a leave for {key}, got {events}"
    assert str(kinds[0]).upper().find("PUT") >= 0, kinds
    assert str(kinds[-1]).upper().find("DELETE") >= 0, kinds


@pytest.mark.e2e
def test_a_concrete_target_query_hits_the_capability_token(session):
    """The trap the specification now names, pinned so it stays named.

    A `*` in a declared token behaves as a pattern, so asking liveliness about
    ONE target returns the source's capability token — for any target id, and
    whether or not that vessel has ever been heard. It means "this source
    publishes about targets", never "this target is live".

    Pinned rather than commented because the reading is so natural: a consumer
    that asks `@target/mmsi_123` and gets a reply has every reason to think it
    asked about MMSI 123.
    """
    key = "keelson/@v0/shore_station/pubsub/location_fix/ais/@target/*"
    token = session.liveliness().declare_token(key)
    time.sleep(0.5)

    try:
        for mmsi in ("mmsi_123456789", "mmsi_000000000"):
            replies = session.liveliness().get(
                f"keelson/@v0/shore_station/pubsub/location_fix/ais/@target/{mmsi}"
            )
            matched = [str(r.ok.key_expr) for r in replies]
            assert matched == [key], (
                f"querying {mmsi} answered with {matched}; liveliness cannot "
                "answer whether a given target exists"
            )
    finally:
        token.undeclare()
