"""keelson.scaffolding.discovery: liveliness is the inventory (keelson #191)."""

import json
from types import SimpleNamespace

import pytest

from keelson.scaffolding import discovery
from keelson.scaffolding.discovery import Interface, Publication

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "key, expected",
    [
        (
            "rise/@v0/sf18/pubsub/location_fix/ardusimple/GGA",
            Publication("sf18", "location_fix", "ardusimple/GGA", False),
        ),
        (
            "rise/@v0/shore/pubsub/location_fix/ais/0/@target",
            Publication("shore", "location_fix", "ais/0", True),
        ),
        ("rise/@v0/sf18/*/ardusimple/GGA", None),  # source-level
        ("rise/@v0/sf18/pubsub/*/ardusimple", None),  # legacy coarse
        ("rise/@v0/sf18/@rpc/configurable/v1/*/platform", None),  # RPC
        ("rise/@v0/sf18/pubsub/location_fix", None),  # no source
        ("rise/sf18/pubsub/location_fix/x", None),  # not keelson
    ],
)
def test_classify_publication(key, expected):
    assert discovery.classify_publication(key) == expected


@pytest.mark.parametrize(
    "key, expected",
    [
        (
            "rise/@v0/sf18/@rpc/configurable/v1/*/platform",
            Interface("sf18", "configurable", "v1", "platform"),
        ),
        (
            "a/b/@v0/rov/@rpc/route_planner/v1/*/planner/0",
            Interface("rov", "route_planner", "v1", "planner/0"),
        ),
        ("rise/@v0/sf18/@rpc/configurable/v1/get_config/platform", None),
        ("rise/@v0/sf18/pubsub/location_fix/gnss", None),
    ],
)
def test_classify_interface(key, expected):
    assert discovery.classify_interface(key) == expected


def _ok(key, payload=b""):
    return SimpleNamespace(
        ok=SimpleNamespace(
            key_expr=key, payload=SimpleNamespace(to_bytes=lambda: payload)
        ),
        err=None,
    )


class _FakeSession:
    def __init__(self, tokens=(), replies=()):
        self.tokens, self.replies, self.selectors = list(tokens), list(replies), []

    def liveliness(self):
        return self

    def get(self, selector, **kwargs):
        self.selectors.append(selector)
        if "@rpc/configurable/v1/get_config" in selector:
            return iter(self.replies)
        targets = selector.endswith("/@target/**")
        return iter(
            _ok(k)
            for k in self.tokens
            if k.endswith("/@target") == targets
            and ("/@rpc/" in k) == ("/@rpc/" in selector)
        )


def test_list_publications_asks_twice_and_keeps_subject_tokens():
    session = _FakeSession(
        tokens=[
            "rise/@v0/sf18/pubsub/location_fix/gnss",
            "rise/@v0/sf18/*/gnss",
            "rise/@v0/shore/pubsub/location_fix/ais/@target",
        ]
    )
    pubs = discovery.list_publications(session, "rise")
    assert pubs == [
        Publication("sf18", "location_fix", "gnss", False),
        Publication("shore", "location_fix", "ais", True),
    ]
    # The plain and the target-scoped patterns are disjoint (spec §5.5).
    assert session.selectors == [
        "rise/@v0/*/pubsub/*/**",
        "rise/@v0/*/pubsub/*/**/@target/**",
    ]


def test_list_interfaces():
    session = _FakeSession(tokens=["rise/@v0/sf18/@rpc/configurable/v1/*/platform"])
    assert discovery.list_interfaces(session, "rise") == [
        Interface("sf18", "configurable", "v1", "platform")
    ]


def test_list_platforms_keeps_only_platform_documents():
    key = "rise/@v0/{}/@rpc/configurable/v1/get_config/{}"
    session = _FakeSession(
        replies=[
            _ok(
                key.format("sf18", "platform"),
                json.dumps({"platform_type": "vessel", "name": "SF18"}).encode(),
            ),
            _ok(key.format("sf18", "warrant"), json.dumps({"claims": {}}).encode()),
            _ok(
                key.format("mast", "geometry/0"),
                json.dumps({"platform_type": "sensor_station"}).encode(),
            ),
            _ok(key.format("x", "broken"), b"not json"),
            SimpleNamespace(ok=None, err=SimpleNamespace()),
        ]
    )
    platforms = discovery.list_platforms(session, "rise")
    assert [
        (p.entity_id, p.source_id, p.config["platform_type"]) for p in platforms
    ] == [
        ("mast", "geometry/0", "sensor_station"),
        ("sf18", "platform", "vessel"),
    ]
    assert session.selectors == ["rise/@v0/*/@rpc/configurable/v1/get_config/**"]
