"""A token vouches for every source segment-prefixed beneath it.

These drive the two liveliness handlers directly with synthetic samples,
because the property under test is invisible at the `SourceLiveliness`
level: that class is told which tokens count, and the question here is
whether the connector routes a token to the right sources in the first
place.

Regression. A producer commonly declares ONE process-level token under its
`--source-id` while publishing under sub-qualified source ids beneath it —
MAVLink fans out to `{source}/gps`, `{source}/imu`, ... under a single
token. The liveliness key space cannot express "any ancestor of this
source": `pubsub/*/mavlink` and `pubsub/*/mavlink/gps` simply do not
intersect, and neither do the source-level pair. Subscribing per-source
therefore saw nothing for every sub-qualified source, and ten of a drone's
eleven sources sat at UNKNOWN while reporting a live publication rate. The
subscriptions are entity-wide and the handlers apply `token_covers_source`.
"""

import pytest

pytestmark = pytest.mark.unit


class _Sample:
    """Minimal stand-in for zenoh.Sample: the handlers read key_expr and kind."""

    def __init__(self, key: str, kind):
        self.key_expr = key
        self.kind = kind


@pytest.fixture
def mod(entity_health_module):
    return entity_health_module


@pytest.fixture
def put(mod):
    import zenoh

    return lambda key: _Sample(key, zenoh.SampleKind.PUT)


@pytest.fixture
def delete(mod):
    import zenoh

    return lambda key: _Sample(key, zenoh.SampleKind.DELETE)


def _watch(mod, source):
    """Register `source` as watched and hand back its liveliness state."""
    from entity_health.evaluator import SourceLiveliness

    state = SourceLiveliness()
    mod.SOURCE_LIVELINESS[source] = state
    return state


class TestSubscriptionKeysReachParentTokens:
    """The defect lived in the key expression, not the handler.

    A handler cannot filter a sample it never receives, so these assert the
    subscription shape directly: the keys `_apply_config` declares must
    intersect a token declared at an ancestor source.
    """

    REALM, ENTITY = "rise", "drone"
    PUBSUB_SUB = f"{REALM}/@v0/{ENTITY}/pubsub/*/**"
    SOURCE_SUB = f"{REALM}/@v0/{ENTITY}/*/**"

    @staticmethod
    def _intersects(sub, declared):
        from zenoh import KeyExpr

        return KeyExpr(sub).intersects(KeyExpr(declared))

    def test_source_subscription_reaches_a_parent_token(self):
        assert self._intersects(
            self.SOURCE_SUB, f"{self.REALM}/@v0/{self.ENTITY}/*/mavlink"
        )

    def test_pubsub_subscription_reaches_a_parent_token(self):
        assert self._intersects(
            self.PUBSUB_SUB,
            f"{self.REALM}/@v0/{self.ENTITY}/pubsub/location_fix/mavlink",
        )

    def test_the_per_source_keys_this_replaced_did_not(self):
        """Why the entity-wide shape is necessary rather than merely tidy."""
        assert not self._intersects(
            f"{self.REALM}/@v0/{self.ENTITY}/*/mavlink/gps",
            f"{self.REALM}/@v0/{self.ENTITY}/*/mavlink",
        )
        assert not self._intersects(
            f"{self.REALM}/@v0/{self.ENTITY}/pubsub/*/mavlink/gps",
            f"{self.REALM}/@v0/{self.ENTITY}/pubsub/location_fix/mavlink",
        )


class TestSourceLevelTokenCoverage:
    def test_parent_token_marks_a_sub_qualified_source_present(self, mod, put):
        """The bug: token at `mavlink`, config expects `mavlink/gps`."""
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_source_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/*/mavlink"))

        assert state.is_present

    def test_exact_token_still_works(self, mod, put):
        state = _watch(mod, "mavlink")
        handler = mod._make_source_liveliness_handler("mavlink")

        handler(put("rise/@v0/drone/*/mavlink"))

        assert state.is_present

    def test_a_sibling_source_is_not_covered(self, mod, put):
        """Entity-wide subscription means every source sees every token."""
        state = _watch(mod, "labjack")
        handler = mod._make_source_liveliness_handler("labjack")

        handler(put("rise/@v0/drone/*/mavlink"))

        assert not state.is_present

    def test_prefix_must_land_on_a_segment_boundary(self, mod, put):
        state = _watch(mod, "mavlink2")
        handler = mod._make_source_liveliness_handler("mavlink2")

        handler(put("rise/@v0/drone/*/mavlink"))

        assert not state.is_present

    def test_a_narrower_token_does_not_cover_its_parent(self, mod, put):
        state = _watch(mod, "mavlink")
        handler = mod._make_source_liveliness_handler("mavlink")

        handler(put("rise/@v0/drone/*/mavlink/gps"))

        assert not state.is_present

    def test_delete_retracts_presence(self, mod, put, delete):
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_source_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/*/mavlink"))
        assert state.is_present
        handler(delete("rise/@v0/drone/*/mavlink"))
        assert not state.is_present

    def test_pubsub_tokens_are_left_to_the_other_handler(self, mod, put):
        """The wide subscription sees them; this handler must not claim them."""
        state = _watch(mod, "mavlink")
        handler = mod._make_source_liveliness_handler("mavlink")

        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink"))

        assert not state.source_tokens


class TestSubjectLevelTokenCoverage:
    """Coverage grants *presence*, but only an exact source grants
    *advertisement*.

    An earlier revision of this file asserted the opposite — that a parent's
    subject token advertises for its children — and that is the bug, not the
    feature. A producer that declares `vehicle_mode` under its bare
    `--source-id` while fanning data out under `mavlink/gps` would have the
    parent's subject credited to the child. The child's advertised set becomes
    non-empty without containing `location_fix`, and `Evaluator.evaluate()`
    reads a non-empty set as "this source has adopted three-tier liveliness",
    concludes row (c) NOT_ADVERTISED, and reports a fault while 1 Hz data flows
    past. Empty is the honest answer: it lands the source on row (d)'s
    activity-based fallback, which is what it got before entity-wide coverage.
    """

    def test_parent_token_grants_presence_but_not_advertisement(self, mod, put):
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink"))

        assert state.is_present, "the declaring process is demonstrably up"
        assert (
            not state.advertised_subjects
        ), "the parent claims location_fix for itself, not for mavlink/gps"

    def test_an_exact_token_does_advertise(self, mod, put):
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink/gps"))

        assert "location_fix" in state.advertised_subjects

    def test_a_parent_retraction_leaves_the_childs_own_token_standing(
        self, mod, put, delete
    ):
        """Two live tokens, one name: the DELETE must retract only its own.

        Both a producer's parent process and its per-device child can advertise
        the same subject for the same watched source. Tracking advertisement by
        subject *name* cannot tell "the last token died" from "one of several
        died", so a parent restart would retract a subject the child is still
        advertising — and never re-PUT it, because the child never restarted.
        """
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink/gps"))
        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink"))
        assert "location_fix" in state.advertised_subjects

        handler(delete("rise/@v0/drone/pubsub/location_fix/mavlink"))

        assert "location_fix" in state.advertised_subjects
        assert state.is_present

    def test_the_last_retraction_does_clear_it(self, mod, put, delete):
        """The counting must not turn into a ratchet."""
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink/gps"))
        handler(delete("rise/@v0/drone/pubsub/location_fix/mavlink/gps"))

        assert not state.advertised_subjects
        assert not state.is_present

    def test_legacy_coarse_token_counts_as_source_presence(self, mod, put):
        state = _watch(mod, "mavlink/gps")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/*/mavlink"))

        assert state.source_tokens
        assert not state.advertised_subjects

    def test_a_sibling_source_is_not_covered(self, mod, put):
        state = _watch(mod, "labjack")
        handler = mod._make_pubsub_liveliness_handler("labjack")

        handler(put("rise/@v0/drone/pubsub/location_fix/mavlink"))

        assert not state.is_present

    def test_source_level_tokens_are_left_to_the_other_handler(self, mod, put):
        state = _watch(mod, "mavlink")
        handler = mod._make_pubsub_liveliness_handler("mavlink")

        handler(put("rise/@v0/drone/*/mavlink"))

        assert not state.is_present


class TestCoverageDoesNotManufactureFaults:
    """The whole point of the coverage rule is to remove spurious faults.

    These drive the handler and then ask `Evaluator.evaluate()` what an
    operator would actually see, because that is where the cost of getting
    the tiers wrong lands.
    """

    @staticmethod
    def _evaluator(mod, source, subject):
        from entity_health.evaluator import Evaluator, Expectation, SourceLiveliness

        state = SourceLiveliness()
        mod.SOURCE_LIVELINESS[source] = state
        exp = Expectation(name=subject, inactive_after_s=10.0, window_s=10.0)
        return Evaluator(exp, liveliness=state), state

    def test_a_parents_other_subject_does_not_fault_the_childs(self, mod, put):
        """The reviewer's scenario, end to end.

        A producer declares `vehicle_mode` under its bare `--source-id` and
        publishes `location_fix` under `mavlink/gps` with no child-level token.
        Crediting the parent's token to the child makes its advertised set
        non-empty but missing `location_fix` — row (c), NOT_ADVERTISED, at full
        publication rate. Row (d) is correct: presence is established, the
        source has not adopted three-tier liveliness *for this source id*, so
        fall back to activity.
        """
        from entity_health.evaluator import HEALTH_NOMINAL

        ev, _state = self._evaluator(mod, "mavlink/gps", "location_fix")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/vehicle_mode/mavlink"))
        for t in range(10):
            ev.record(float(t))

        assert ev.evaluate(9.5).level == HEALTH_NOMINAL

    def test_an_exact_token_for_another_subject_still_faults(self, mod, put):
        """Row (c) must still fire where it genuinely applies.

        Same shape, but the tokens name the watched source exactly — a real
        three-tier adopter that does not claim `location_fix`. That is a config
        error worth surfacing, and the fix above must not swallow it.
        """
        from entity_health.evaluator import HEALTH_NOT_ADVERTISED

        ev, _state = self._evaluator(mod, "mavlink/gps", "location_fix")
        handler = mod._make_pubsub_liveliness_handler("mavlink/gps")

        handler(put("rise/@v0/drone/pubsub/vehicle_mode/mavlink/gps"))
        for t in range(10):
            ev.record(float(t))

        assert ev.evaluate(9.5).level == HEALTH_NOT_ADVERTISED
