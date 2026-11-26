"""Route helpers (keelson.routes, keelson.scaffolding.routes): the §6 choreography."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

import keelson
from keelson import routes
from keelson.payloads.Route_pb2 import (
    ROUTE_TOPOLOGY_CLOSED,
    Route,
    RouteRef,
)
from keelson.payloads.RouteChangeEvent_pb2 import RouteChangeEvent
from keelson.payloads.RouteEditAuthority_pb2 import RouteEditAuthority

BASE, ENTITY = "rise", "sf18"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _route(route_id="r7", edition=3, n_waypoints=3) -> Route:
    r = Route(route_id=route_id, route_edition_number=edition)
    for i in range(n_waypoints):
        wp = r.waypoints.waypoint.add()
        wp.id = f"wp{i}"
    return r


# ---------------------------------------------------------------- keys (§6.3)


@pytest.mark.unit
def test_the_edition_store_keys():
    assert (
        routes.construct_route_edition_key(BASE, ENTITY, "r7", 4)
        == "rise/@v0/sf18/pubsub/route/r7/edition/4"
    )
    assert (
        routes.construct_route_latest_key(BASE, ENTITY, "r7")
        == "rise/@v0/sf18/pubsub/route/r7/latest"
    )
    assert (
        routes.construct_route_change_event_key(BASE, ENTITY, "r7", "c1")
        == "rise/@v0/sf18/pubsub/route_change_event/r7/c1"
    )
    assert (
        routes.construct_route_signature_key(BASE, ENTITY, "r7", 4)
        == "rise/@v0/sf18/pubsub/route_signature/r7/edition/4"
    )
    assert routes.construct_route_status_key(BASE, ENTITY, "r7").endswith(
        "/pubsub/route_status/r7"
    )
    assert routes.construct_route_edit_authority_key(BASE, ENTITY, "r7").endswith(
        "/pubsub/route_edit_authority/r7"
    )
    assert routes.construct_route_edit_request_key(BASE, ENTITY, "r7").endswith(
        "/pubsub/route_edit_request/r7"
    )


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "a/b", "r*", None])
def test_a_route_id_is_one_key_chunk(bad):
    with pytest.raises(ValueError):
        routes.construct_route_latest_key(BASE, ENTITY, bad)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_an_edition_is_a_positive_integer(bad):
    with pytest.raises(ValueError):
        routes.construct_route_edition_key(BASE, ENTITY, "r7", bad)


@pytest.mark.unit
def test_parse_round_trips_editions_and_latest():
    k = routes.parse_route_key(
        routes.construct_route_edition_key(BASE, ENTITY, "r7", 4)
    )
    assert k == routes.RouteKey(BASE, ENTITY, "r7", "edition", 4)
    k = routes.parse_route_key(routes.construct_route_latest_key(BASE, ENTITY, "r7"))
    assert k == routes.RouteKey(BASE, ENTITY, "r7", "latest", None)


@pytest.mark.unit
@pytest.mark.parametrize(
    "key",
    [
        "rise/@v0/sf18/pubsub/route/navigator",  # a declared route: no editions
        "rise/@v0/sf18/pubsub/route/r7/edition/0",
        "rise/@v0/sf18/pubsub/route/r7/edition/x",
        "rise/@v0/sf18/pubsub/route/r7/latest/extra",
        "rise/@v0/sf18/pubsub/route_status/r7",
        "rise/v0/sf18/pubsub/route/r7/latest",  # the legacy key form
    ],
)
def test_parse_refuses_every_other_shape(key):
    assert routes.parse_route_key(key) is None


# ------------------------------------------------------- the bump (§6.4)


@pytest.mark.unit
@pytest.mark.parametrize(
    "local,latest,next_exists,kind",
    [
        (3, 3, False, None),
        (0, None, False, None),  # a new route
        (3, None, False, None),  # nothing stored for it
        (3, 3, True, "next-exists"),
        (3, 4, False, "latest-moved"),  # somebody published past us
        (3, 2, False, "latest-moved"),  # behind is not clean either
    ],
)
def test_edition_conflict(local, latest, next_exists, kind):
    c = routes.edition_conflict(local, latest, next_exists)
    assert (c.kind if c else None) == kind


@pytest.mark.unit
def test_bump_edition_publishes_edition_then_latest_then_the_change_event():
    route = _route(edition=3)
    bump = routes.bump_edition(
        BASE,
        ENTITY,
        route,
        author_id="anna@ROC-1",
        author_site="ROC-1",
        reason="weather reroute",
        now=NOW,
        change_id="c-9",
    )
    keys = [k for k, _ in bump.publications]
    assert keys == [
        "rise/@v0/sf18/pubsub/route/r7/edition/4",
        "rise/@v0/sf18/pubsub/route/r7/latest",
        "rise/@v0/sf18/pubsub/route_change_event/r7/c-9",
    ]
    assert route.route_edition_number == 3  # the input is not modified
    assert bump.route.route_edition_number == 4
    assert bump.route.modified_at.ToDatetime(tzinfo=timezone.utc) == NOW
    # The edition and latest carry the same bytes.
    assert bump.publications[0][1] == bump.publications[1][1]
    _, _, payload = keelson.uncover(bump.publications[0][1])
    assert Route.FromString(payload).route_edition_number == 4
    _, _, payload = keelson.uncover(bump.publications[2][1])
    event = RouteChangeEvent.FromString(payload)
    assert (event.old_edition, event.new_edition, event.change_id) == (3, 4, "c-9")
    assert (event.author_id, event.author_site, event.reason) == (
        "anna@ROC-1",
        "ROC-1",
        "weather reroute",
    )


@pytest.mark.unit
def test_a_bumped_edition_signs_over_its_stored_payload():
    bump = routes.bump_edition(BASE, ENTITY, _route(), author_id="a", now=NOW)
    stored = bump.publications[0][1]
    signing = routes.edition_signing_input(stored)
    assert signing == bump.route.SerializeToString()
    assert Route.FromString(signing).route_edition_number == 4


# ------------------------------------------------- waypoint defaults (§6.2)


@pytest.mark.unit
def test_a_present_value_wins_even_zero_and_an_absent_one_takes_the_default():
    route = _route(n_waypoints=3)
    route.waypoints.defaults.radius_m = 50
    route.waypoints.defaults.planned_sog_knots = 8
    wps = route.waypoints.waypoint
    wps[0].radius_m = 0.0  # an explicit zero: an answer, not an absence
    wps[1].planned_sog_knots = 5

    merged = routes.merge_waypoint_defaults(route)
    assert [m.radius_m for m in merged] == [0.0, 50, 50]
    assert [m.planned_sog_knots for m in merged] == [8, 5, 8]
    assert not route.waypoints.waypoint[2].HasField("radius_m")  # input untouched


@pytest.mark.unit
def test_leg_defaults_fill_only_outgoing_legs_of_an_open_route():
    route = _route(n_waypoints=3)
    route.waypoints.defaults.leg.xtd_port_m = 30
    route.waypoints.defaults.leg.sog_max_knots = 12
    route.waypoints.defaults.leg.xtd_corridor_mode = 1  # never taken from defaults
    route.waypoints.waypoint[1].leg.xtd_port_m = 10

    merged = routes.merge_waypoint_defaults(route)
    assert merged[0].leg.xtd_port_m == 30 and merged[0].leg.sog_max_knots == 12
    assert merged[1].leg.xtd_port_m == 10 and merged[1].leg.sog_max_knots == 12
    assert not merged[2].HasField("leg")  # the last waypoint has no outgoing leg
    assert merged[0].leg.xtd_corridor_mode == 0


@pytest.mark.unit
def test_a_closed_route_closes_with_a_leg_on_the_last_waypoint():
    route = _route(n_waypoints=3)
    route.waypoints.topology = ROUTE_TOPOLOGY_CLOSED
    route.waypoints.defaults.leg.xtd_port_m = 30
    merged = routes.merge_waypoint_defaults(route)
    assert [m.leg.xtd_port_m for m in merged] == [30, 30, 30]


@pytest.mark.unit
def test_no_defaults_changes_nothing():
    route = _route()
    merged = routes.merge_waypoint_defaults(route)
    assert [m.SerializeToString() for m in merged] == [
        w.SerializeToString() for w in route.waypoints.waypoint
    ]


# ---------------------------------------------------------- the lease (§6.5.1)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _lease(token="t1", controller="anna@ROC-1", ttl=30, released=False):
    a = RouteEditAuthority(
        route_id="r7",
        controller_id=controller,
        token=token,
        lease_ttl_seconds=ttl,
        released=released,
    )
    return a


@pytest.mark.unit
def test_expiry_is_on_the_receivers_clock_and_expires_at_is_never_read():
    clock = _Clock()
    tracker = routes.LeaseTracker(clock=clock)
    lease = _lease()
    lease.expires_at.FromDatetime(datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert tracker.observe(lease)  # an expires_at in the past is not "expired"
    assert tracker.holder().controller_id == "anna@ROC-1"
    clock.t += 29.9
    assert tracker.holder() is not None
    clock.t += 0.2
    assert tracker.expired()


@pytest.mark.unit
def test_a_heartbeat_re_arms_and_a_foreign_token_waits_for_the_lapse():
    clock = _Clock()
    tracker = routes.LeaseTracker(clock=clock)
    tracker.observe(_lease("t1"))
    clock.t += 20
    assert tracker.observe(_lease("t1"))  # heartbeat
    clock.t += 20
    assert tracker.holder() is not None  # 40 s after the grant, 20 after the beat
    assert not tracker.observe(_lease("t2", "ben@ROC-2"))  # held: ignored
    assert tracker.holder().token == "t1"
    clock.t += 31
    assert tracker.observe(_lease("t2", "ben@ROC-2"))  # lapsed here: taken
    assert tracker.holder().controller_id == "ben@ROC-2"


@pytest.mark.unit
def test_only_a_release_with_the_current_token_releases():
    tracker = routes.LeaseTracker(clock=_Clock())
    tracker.observe(_lease("t1"))
    assert not tracker.observe(_lease("t9", released=True))
    assert tracker.holder() is not None
    assert tracker.observe(_lease("t1", released=True))
    assert tracker.holder() is None


@pytest.mark.unit
def test_a_missing_ttl_uses_the_default():
    clock = _Clock()
    tracker = routes.LeaseTracker(default_ttl_seconds=5, clock=clock)
    tracker.observe(_lease(ttl=0))
    assert tracker.remaining() == pytest.approx(5)


# ------------------------------------------- over zenoh (keelson.scaffolding)


class _Store:
    """Stands in for the router's storage_manager: remembers the last sample
    per key and answers gets — enough to exercise the read-before-write."""

    def __init__(self, session, expr):
        self.data = {}
        self.order = []
        self._sub = session.declare_subscriber(expr, self._on_sample)
        self._q = session.declare_queryable(expr, self._on_query, complete=False)

    def _on_sample(self, sample):
        key = str(sample.key_expr)
        self.data[key] = bytes(sample.payload.to_bytes())
        self.order.append(key)

    def _on_query(self, query):
        import zenoh

        for key, value in list(self.data.items()):
            if zenoh.KeyExpr(key).intersects(query.key_expr):
                query.reply(key, value)

    def close(self):
        self._sub.undeclare()
        self._q.undeclare()

    def wait_for(self, n, timeout=3.0):
        deadline = time.time() + timeout
        while len(self.order) < n and time.time() < deadline:
            time.sleep(0.05)
        return len(self.order) >= n


@pytest.fixture
def zenoh_store():
    import zenoh

    conf = zenoh.Config()
    conf.insert_json5("scouting/multicast/enabled", "false")
    session = zenoh.open(conf)
    store = _Store(session, f"{BASE}/@v0/**")
    try:
        yield session, store
    finally:
        store.close()
        session.close()


@pytest.mark.e2e
def test_publish_edition_bump_reads_the_store_then_publishes_in_order(zenoh_store):
    from keelson.scaffolding import publish_edition_bump

    session, store = zenoh_store
    first = publish_edition_bump(
        session, BASE, ENTITY, _route(edition=0), author_id="a", timeout=1.0
    )
    assert store.wait_for(3)
    assert [k.split("/pubsub/")[1] for k in store.order] == [
        "route/r7/edition/1",
        "route/r7/latest",
        f"route_change_event/r7/{first.change_event.change_id}",
    ]
    second = publish_edition_bump(
        session, BASE, ENTITY, first.route, author_id="a", timeout=1.0
    )
    assert second.route.route_edition_number == 2


@pytest.mark.e2e
def test_publish_edition_bump_refuses_when_the_store_moved_on(zenoh_store):
    from keelson.scaffolding import EditionConflict, publish_edition_bump

    session, store = zenoh_store
    one = publish_edition_bump(
        session, BASE, ENTITY, _route(edition=0), author_id="a", timeout=1.0
    )
    publish_edition_bump(session, BASE, ENTITY, one.route, author_id="b", timeout=1.0)
    assert store.wait_for(6)
    # A second editor still on edition 1 would overwrite edition 2.
    with pytest.raises(EditionConflict) as refused:
        publish_edition_bump(
            session, BASE, ENTITY, one.route, author_id="c", timeout=1.0
        )
    assert refused.value.conflict.kind == "next-exists"
    time.sleep(0.2)
    assert len(store.order) == 6  # nothing was published


@pytest.mark.e2e
def test_resolve_route_ref_returns_the_edition_or_surfaces_the_dangling_one(
    zenoh_store,
):
    from keelson.scaffolding import (
        DanglingRouteRef,
        publish_edition_bump,
        resolve_route_ref,
    )

    session, store = zenoh_store
    publish_edition_bump(
        session, BASE, ENTITY, _route(edition=0), author_id="a", timeout=1.0
    )
    assert store.wait_for(3)
    route, envelope = resolve_route_ref(
        session,
        BASE,
        ENTITY,
        RouteRef(route_id="r7", route_edition_number=1),
        timeout=1.0,
    )
    assert route.route_edition_number == 1
    assert routes.edition_signing_input(envelope) == route.SerializeToString()
    with pytest.raises(DanglingRouteRef):
        resolve_route_ref(
            session,
            BASE,
            ENTITY,
            RouteRef(route_id="r7", route_edition_number=2),
            timeout=1.0,
        )
