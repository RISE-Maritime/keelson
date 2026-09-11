"""Tests for the operational envelope pair (protocol-specification.md §6.3).

Two messages model one feature. `RouteExecution.envelope_limits` is the live
standing — is the vessel outside its envelope right now — and is an ephemeral 1 Hz
snapshot the router does not persist. `envelope_exceedance` is the durable half:
the record that a limit was breached, how long before the system noticed, and what
it did.

These cover the rules a `.proto` cannot state on its own, and one that it can but
which matters enough to pin: `RouteExecution` is live on the wire, so the fields
added beside the existing deviation flag must not disturb it.
"""

import keelson

from keelson import qos
from keelson.payloads.EnvelopeExceedance_pb2 import EnvelopeExceedance
from keelson.payloads.Route_pb2 import (
    ENVELOPE_BOUND_LOWER,
    ENVELOPE_BOUND_UNSPECIFIED,
    ENVELOPE_BOUND_UPPER,
    RouteInfo,
    RouteRef,
)
from keelson.payloads.RouteExecution_pb2 import EnvelopeLimitState, RouteExecution


def _field_names(message_class):
    return {field.name for field in message_class.DESCRIPTOR.fields}


def test_the_subject_resolves_and_travels_elevated():
    """A breach reports a condition true *now*, not a concluded log entry."""
    assert keelson.is_subject_well_known("envelope_exceedance")
    assert (
        keelson.get_subject_schema("envelope_exceedance")
        == "keelson.EnvelopeExceedance"
    )
    assert qos.profile_name_for("envelope_exceedance") == "elevated"


def test_both_key_tokens_are_single_strings():
    """§6.3 keys this `envelope_exceedance/{voyage_id}/{exceedance_id}`.

    A composite id in either position publishes without error and never persists —
    the storage's `.../envelope_exceedance/*/*` expression simply does not match it.
    """
    for name in ("voyage_id", "exceedance_id"):
        field = EnvelopeExceedance.DESCRIPTOR.fields_by_name[name]
        assert field.type == field.TYPE_STRING
        assert not field.is_repeated


def test_voyage_id_is_required_not_optional():
    """A RouteRef identifies the plan edition, not the sailing.

    The same route sailed twice produces records attributable to a voyage only by
    guessing at time windows — and voyage is the correlation key an assessment
    reads by. It is also the leading key token, so an absent value yields an
    unaddressable key.
    """
    field = EnvelopeExceedance.DESCRIPTOR.fields_by_name["voyage_id"]
    assert not field.has_presence

    # route_ref stays optional and stays a ref, not a bare id.
    route_ref = EnvelopeExceedance.DESCRIPTOR.fields_by_name["route_ref"]
    assert route_ref.containing_oneof is not None
    assert route_ref.message_type.full_name == "keelson.RouteRef"
    assert "route_id" not in _field_names(EnvelopeExceedance)


def test_the_response_is_a_list_because_reactions_compound():
    """A breach can be alerted AND speed-limited; a scalar forced a choice."""
    field = EnvelopeExceedance.DESCRIPTOR.fields_by_name["responses"]
    assert field.is_repeated

    record = EnvelopeExceedance(
        responses=[
            EnvelopeExceedance.RESPONSE_ALERTED,
            EnvelopeExceedance.RESPONSE_SPEED_LIMIT,
        ]
    )
    decoded = EnvelopeExceedance.FromString(record.SerializeToString())
    assert list(decoded.responses) == [
        EnvelopeExceedance.RESPONSE_ALERTED,
        EnvelopeExceedance.RESPONSE_SPEED_LIMIT,
    ]


def test_deciding_not_to_act_is_a_member_not_an_empty_list():
    """ "We saw it and chose not to act" is a finding.

    It must not be indistinguishable from a producer that never filled the field
    in — which is exactly what an empty list means.
    """
    assert EnvelopeExceedance.RESPONSE_NONE != EnvelopeExceedance.RESPONSE_UNSPECIFIED

    chose_not_to = EnvelopeExceedance(responses=[EnvelopeExceedance.RESPONSE_NONE])
    never_said = EnvelopeExceedance()
    assert chose_not_to.SerializeToString() != never_said.SerializeToString()
    assert len(never_said.responses) == 0


def test_a_bound_direction_exists_on_both_halves():
    """`peak_value` — "the most extreme value" — is undefined without it.

    RouteInfo carries limits in both directions (max_wind_gust_mps breaches above,
    min_visibility_m below), so nothing about a limit's identity says which way it
    fails.
    """
    for message_class in (EnvelopeExceedance, EnvelopeLimitState):
        field = message_class.DESCRIPTOR.fields_by_name["bound"]
        assert field.enum_type.full_name == "keelson.EnvelopeBound"

    assert ENVELOPE_BOUND_UNSPECIFIED == 0
    assert ENVELOPE_BOUND_UPPER != ENVELOPE_BOUND_LOWER

    # Both directions are actually authorable on the route the limits come from.
    limits = _field_names(RouteInfo)
    assert "max_wind_gust_mps" in limits
    assert "min_visibility_m" in limits


def test_an_unset_bound_is_distinguishable_from_upper():
    """A producer that never said which way a limit fails must not read as UPPER.

    `peak_value` is defined against `bound`, so a default silently meaning "upper"
    would report the HIGHEST value reached for a `min_visibility_m` breach — where
    the finding is the lowest. UNSPECIFIED is the same refusal to guess that
    STATE_UNMEASURED makes one level up.
    """
    for message_class in (EnvelopeExceedance, EnvelopeLimitState):
        unsaid = message_class(limit_id="min_visibility_m")
        upper = message_class(limit_id="min_visibility_m", bound=ENVELOPE_BOUND_UPPER)

        assert unsaid.bound == ENVELOPE_BOUND_UNSPECIFIED
        assert unsaid.bound != ENVELOPE_BOUND_UPPER
        assert unsaid.SerializeToString() != upper.SerializeToString()
        assert (
            message_class.FromString(upper.SerializeToString()).bound
            == ENVELOPE_BOUND_UPPER
        )


def test_every_limit_id_example_names_a_real_route_info_field():
    """The proto comments teach `limit_id` by example, so the examples must exist.

    `limit_id` is a documented naming convention rather than an enum (#226 has not
    settled what names limits), which makes the worked examples the only thing
    holding the convention up.
    """
    limits = _field_names(RouteInfo)
    for example in (
        "max_wind_gust_mps",
        "min_visibility_m",
        "max_roll_deg",
        "min_speed_knots",
    ):
        assert example in limits, f"proto comments cite RouteInfo.{example}"


def test_unmeasured_is_distinguishable_from_a_measured_zero():
    """The distinction the State enum exists for.

    A `false` for a limit nothing measures is indistinguishable from a limit being
    honoured, and an unmonitored constraint that reads as compliance is worse than
    an absent one: it looks managed.
    """
    unmeasured = EnvelopeLimitState(
        limit_id="max_wind_gust_mps",
        limit_value=20.0,
        state=EnvelopeLimitState.STATE_UNMEASURED,
    )
    measured_zero = EnvelopeLimitState(
        limit_id="max_wind_gust_mps",
        limit_value=20.0,
        measured_value=0.0,
        state=EnvelopeLimitState.STATE_WITHIN,
    )

    assert not unmeasured.HasField("measured_value")
    assert measured_zero.HasField("measured_value")
    assert unmeasured.SerializeToString() != measured_zero.SerializeToString()


def test_a_measurement_can_be_dated_so_a_stale_one_is_not_read_as_compliance():
    """Without `measured_at` a ten-minute-old reading is STATE_WITHIN exactly like
    a fresh one — the same failure STATE_UNMEASURED prevents, one level down."""
    field = EnvelopeLimitState.DESCRIPTOR.fields_by_name["measured_at"]
    assert field.containing_oneof is not None, "measured_at should be declared optional"
    assert field.message_type.full_name == "google.protobuf.Timestamp"


def test_envelope_provenance_mirrors_the_authority_policy_pair():
    """So a recorded breach can be replayed against the limits actually in force.

    If the envelope varies with context (#226), a recorded 8 m/s breach cannot
    otherwise distinguish the right limit correctly applied from a stale one
    wrongly applied — and the second is a monitoring failure this record catches.
    """
    names = _field_names(EnvelopeExceedance)
    assert {"envelope_id", "envelope_config_digest"} <= names

    digest = EnvelopeExceedance.DESCRIPTOR.fields_by_name["envelope_config_digest"]
    assert digest.type == digest.TYPE_BYTES
    assert digest.has_presence  # a bytes field only has presence when optional


def test_the_three_latency_timestamps_are_separable():
    """crossed/detected/responded answer different questions, and absence is real.

    `crossed_at` is optional because a producer sampling at an interval knows when
    it *saw* the crossing, not when it happened; an interpolated guess published as
    a measured instant would corrupt the very figure this message carries.
    """
    # A message field always has presence in proto3, so the explicit `optional`
    # keyword is what is being pinned here — it surfaces as a synthetic oneof.
    for name in ("crossed_at", "responded_at", "cleared_at"):
        field = EnvelopeExceedance.DESCRIPTOR.fields_by_name[name]
        assert field.containing_oneof is not None, f"{name} should be declared optional"

    # detected_at is the one always known, so it is deliberately not optional.
    assert (
        EnvelopeExceedance.DESCRIPTOR.fields_by_name["detected_at"].containing_oneof
        is None
    )

    open_breach = EnvelopeExceedance(exceedance_id="e-1", voyage_id="v-1")
    assert not open_breach.HasField("cleared_at")

    cleared_at_epoch = EnvelopeExceedance(exceedance_id="e-1", voyage_id="v-1")
    cleared_at_epoch.cleared_at.FromSeconds(0)
    assert cleared_at_epoch.HasField("cleared_at")
    assert open_breach.SerializeToString() != cleared_at_epoch.SerializeToString()


def test_latency_arithmetic_survives_an_envelope_round_trip():
    record = EnvelopeExceedance(
        exceedance_id="8f14e45f-ea2b-4b8f-9a1e-000000000001",
        voyage_id="v-2026-08-26-01",
        limit_id="max_wind_gust_mps",
        bound=ENVELOPE_BOUND_UPPER,
        limit_value=20.0,
        measured_value=23.5,
        route_ref=RouteRef(route_id="r-1", route_edition_number=4),
        responses=[EnvelopeExceedance.RESPONSE_ALERTED],
    )
    record.crossed_at.FromSeconds(1_760_000_000)
    record.detected_at.FromSeconds(1_760_000_004)
    record.responded_at.FromSeconds(1_760_000_009)

    envelope = keelson.enclose(record.SerializeToString())
    _received_at, _enclosed_at, payload = keelson.uncover(envelope)
    decoded = EnvelopeExceedance.FromString(payload)

    detection_latency = decoded.detected_at.seconds - decoded.crossed_at.seconds
    reaction_latency = decoded.responded_at.seconds - decoded.detected_at.seconds
    assert detection_latency == 4
    assert reaction_latency == 5
    assert decoded.route_ref.route_edition_number == 4
    assert decoded.bound == ENVELOPE_BOUND_UPPER


def test_route_execution_stays_compatible_both_ways():
    """The check that matters: RouteExecution is live on the wire.

    A message from a publisher that predates `envelope_limits` must parse with the
    field empty, and the existing XTD deviation fields must survive untouched
    alongside it — cross-track is per-leg geometry, not a RouteInfo envelope limit,
    and it was already published.
    """
    old_shape = RouteExecution(
        voyage_id="v-1",
        deviation_active=True,
        deviation_threshold_m=50.0,
        speed_over_ground_knots=12.0,
    )
    decoded = RouteExecution.FromString(old_shape.SerializeToString())
    assert list(decoded.envelope_limits) == []
    assert decoded.deviation_active is True
    assert decoded.deviation_threshold_m == 50.0

    new_shape = RouteExecution(
        voyage_id="v-1",
        deviation_active=True,
        deviation_threshold_m=50.0,
        envelope_limits=[
            EnvelopeLimitState(
                limit_id="max_wind_gust_mps",
                limit_value=20.0,
                measured_value=23.5,
                state=EnvelopeLimitState.STATE_BREACHED,
                bound=ENVELOPE_BOUND_UPPER,
            )
        ],
    )
    round_tripped = RouteExecution.FromString(new_shape.SerializeToString())
    assert round_tripped.deviation_active is True
    assert round_tripped.deviation_threshold_m == 50.0
    assert round_tripped.envelope_limits[0].state == EnvelopeLimitState.STATE_BREACHED

    # The deviation fields keep their numbers; envelope_limits took a free one.
    assert RouteExecution.DESCRIPTOR.fields_by_name["deviation_active"].number == 30
    assert (
        RouteExecution.DESCRIPTOR.fields_by_name["deviation_threshold_m"].number == 31
    )
    assert RouteExecution.DESCRIPTOR.fields_by_name["envelope_limits"].number == 32
